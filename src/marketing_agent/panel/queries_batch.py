"""Leitura do lote semanal: as publicacoes de uma semana com o texto mais recente e o historico.

Funcoes de leitura sobre uma `Session` do chamador; todas filtram pela marca do painel. O
numero de consultas e fixo (nao cresce com as publicacoes): posts, ultimas revisoes, decisoes,
trabalhos ativos e titulos da base. A validade na data da publicacao nao se consulta aqui: vem de
`KbService.valid_refs`, injetado como `ValidRefs`, para ser a mesma selecao da geracao e da edicao.
"""

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from marketing_agent.content.revision_store import kb_ref_ids
from marketing_agent.db.models import ACTIVE_JOB_STATUSES, Approval, Job, Post, PostRevision
from marketing_agent.jobs.queue import GENERATE_POST_JOB
from marketing_agent.kb.service import KbEntry
from marketing_agent.panel.queries_content import IN_REVIEW, kb_titles

ValidRefs = Callable[[str, Sequence[str], date], Mapping[str, KbEntry]]

MAX_BATCH_POSTS: Final = 50  # o teto semanal e 7; isto so protege contra dados anormais
MAX_BATCH_DECISIONS: Final = 300
AGENT_ORIGIN: Final = "agente"
PLANNED: Final = "planned"
DRAFTING: Final = "drafting"


@dataclass(frozen=True)
class ClaimView:
    """Alegacao de uma revisao, com a origem na base.

    `kb_title` e o titulo do item citado (mesmo que ja nao valha); `is_supported` e verdadeiro
    so se o item citado esta ativo e valido na data da publicacao. Sem `kb_ref` nao ha origem a
    verificar (`is_cited` falso).
    """

    text: str
    kb_title: str | None
    is_cited: bool
    is_supported: bool


@dataclass(frozen=True)
class DecisionView:
    """Decisao do responsavel sobre uma revisao do post."""

    rev_no: int
    decision: str
    decided_by: str
    decided_at: datetime
    comment: str | None


@dataclass(frozen=True)
class RevisionView:
    """Texto mais recente de um post, com o parecer do Revisor (so nas revisoes do agente)."""

    id: str
    rev_no: int
    origin: str
    caption: str
    hashtags: list[str]
    call_to_action: str | None
    alt_text: str | None
    claims: list[ClaimView]
    verdict: str | None
    violations: list[str]
    notes: str | None
    created_at: datetime


@dataclass(frozen=True)
class PostCard:
    """Publicacao do lote: estado, ultima revisao e historico de decisoes.

    As propriedades `can_*` espelham o que o `ReviewService` aceita; o servico e que decide.
    """

    id: str
    scheduled_date: date
    channels: list[str]
    theme: str
    lifecycle: str
    review_rounds: int
    last_error: str | None
    has_active_job: bool
    revision: RevisionView | None
    decisions: list[DecisionView]

    @property
    def is_generating(self) -> bool:
        """Por gerar ou a gerar: ha trabalho ativo ou nada falhou ainda (justifica o polling)."""
        waiting = self.lifecycle in (PLANNED, DRAFTING)
        return waiting and (self.has_active_job or self.last_error is None)

    @property
    def can_decide(self) -> bool:
        """Aprovar, rejeitar: a publicacao esta `in_review` e tem texto sobre o qual decidir."""
        return self.lifecycle == IN_REVIEW and self.revision is not None

    @property
    def can_request_changes(self) -> bool:
        """Como `can_decide`, e sem geracao ativa (o servico recusa duas geracoes em paralelo)."""
        return self.can_decide and not self.has_active_job

    @property
    def can_edit(self) -> bool:
        """`in_review`, ou `drafting` com geracao falhada e nada a refaze-la; precisa de texto."""
        if self.revision is None:
            return False
        if self.lifecycle == IN_REVIEW:
            return True
        failed = self.last_error is not None and not self.has_active_job
        return self.lifecycle == DRAFTING and failed


@dataclass(frozen=True)
class BatchView:
    """Lote de uma semana: cartoes por data e totais por estado."""

    week: str
    cards: list[PostCard]

    @property
    def is_polling(self) -> bool:
        """Verdadeiro enquanto alguma publicacao esta por gerar ou a gerar."""
        return any(card.is_generating for card in self.cards)

    @property
    def counts(self) -> list[tuple[str, int]]:
        """(estado, numero) por estado presente, pela ordem em que aparecem."""
        return list(Counter(card.lifecycle for card in self.cards).items())


@dataclass(frozen=True)
class WeekRows:
    """Linhas lidas da base para um lote (antes de juntar a validade da base)."""

    posts: list[Post]
    revisions: dict[str, PostRevision]
    decisions: dict[str, list[DecisionView]]
    active_post_ids: set[str]
    titles: dict[str, str]


def post_week(session: Session, brand_id: str, post_id: str) -> str | None:
    """Semana ISO (`AAAA-Www`) do post desta marca; `None` se nao existe ou e de outra marca."""
    slot_key = session.scalar(
        select(Post.slot_key).where(Post.id == post_id, Post.brand_id == brand_id)
    )
    return None if slot_key is None else slot_key.rsplit("-", 1)[0]


def load_week(session: Session, brand_id: str, week: str) -> WeekRows:
    """Le as publicacoes da semana e o que as acompanha, com um numero fixo de consultas."""
    posts = list(
        session.scalars(
            select(Post)
            .where(Post.brand_id == brand_id, Post.slot_key.startswith(f"{week}-", autoescape=True))
            .order_by(Post.scheduled_date, Post.slot_key)
            .limit(MAX_BATCH_POSTS)
        )
    )
    ids = [post.id for post in posts]
    if not ids:
        return WeekRows([], {}, {}, set(), {})
    revisions = _latest_revisions(session, brand_id, ids)
    claim_refs = {ref for rev in revisions.values() for ref in _claim_refs(rev)}
    return WeekRows(
        posts=posts,
        revisions=revisions,
        decisions=_decisions(session, brand_id, ids),
        active_post_ids=_posts_with_active_job(session, ids),
        titles=kb_titles(session, brand_id, claim_refs),
    )


def build_batch(brand_id: str, week: str, rows: WeekRows, valid_refs: ValidRefs) -> BatchView:
    """Cartoes do lote; a validade de cada alegacao e a da selecao da base da publicacao."""
    cards = [_card(brand_id, post, rows, valid_refs) for post in rows.posts]
    return BatchView(week, cards)


def _latest_revisions(
    session: Session, brand_id: str, post_ids: Collection[str]
) -> dict[str, PostRevision]:
    statement = (
        select(PostRevision)
        .where(PostRevision.brand_id == brand_id, PostRevision.post_id.in_(list(post_ids)))
        .ext(distinct_on(PostRevision.post_id))
        .order_by(PostRevision.post_id, PostRevision.rev_no.desc())
    )
    return {revision.post_id: revision for revision in session.scalars(statement)}


def _decisions(
    session: Session, brand_id: str, post_ids: Collection[str]
) -> dict[str, list[DecisionView]]:
    statement = (
        select(Approval, PostRevision.rev_no)
        .join(PostRevision, PostRevision.id == Approval.revision_id)
        .where(Approval.brand_id == brand_id, Approval.post_id.in_(list(post_ids)))
        .order_by(Approval.decided_at.desc(), Approval.id)
        .limit(MAX_BATCH_DECISIONS)
    )
    by_post: dict[str, list[DecisionView]] = {}
    for approval, rev_no in session.execute(statement):
        view = DecisionView(
            rev_no, approval.decision, approval.decided_by, approval.decided_at, approval.comment
        )
        by_post.setdefault(approval.post_id, []).append(view)
    for views in by_post.values():
        views.reverse()  # do mais antigo para o mais recente
    return by_post


def _posts_with_active_job(session: Session, post_ids: Collection[str]) -> set[str]:
    post_id = Job.payload["post_id"].astext
    statement = select(post_id).where(
        Job.type == GENERATE_POST_JOB,
        Job.status.in_(ACTIVE_JOB_STATUSES),
        post_id.in_(list(post_ids)),
    )
    return set(session.scalars(statement))


def _claim_refs(revision: PostRevision) -> list[str]:
    return [ref for claim in revision.claims if isinstance(ref := claim.get("kb_ref"), str)]


def _card(brand_id: str, post: Post, rows: WeekRows, valid_refs: ValidRefs) -> PostCard:
    stored = rows.revisions.get(post.id)
    revision = (
        None if stored is None else _revision_view(brand_id, post, stored, rows.titles, valid_refs)
    )
    return PostCard(
        id=post.id,
        scheduled_date=post.scheduled_date,
        channels=list(post.channels),
        theme=str(post.brief.get("tema", "")),
        lifecycle=post.lifecycle,
        review_rounds=post.review_rounds,
        last_error=post.last_error,
        has_active_job=post.id in rows.active_post_ids,
        revision=revision,
        decisions=rows.decisions.get(post.id, []),
    )


def _revision_view(
    brand_id: str,
    post: Post,
    revision: PostRevision,
    titles: Mapping[str, str],
    valid_refs: ValidRefs,
) -> RevisionView:
    # A mesma selecao da geracao e da edicao: so os itens do brief que valem na data da publicacao.
    cited = _claim_refs(revision)
    supported = valid_refs(brand_id, kb_ref_ids(post.brief), post.scheduled_date) if cited else {}
    is_agent = revision.origin == AGENT_ORIGIN
    return RevisionView(
        id=revision.id,
        rev_no=revision.rev_no,
        origin=revision.origin,
        caption=revision.caption,
        hashtags=list(revision.hashtags),
        call_to_action=revision.call_to_action,
        alt_text=revision.alt_text,
        claims=[_claim_view(claim, titles, supported) for claim in revision.claims],
        verdict=revision.review_verdict if is_agent else None,
        violations=list(revision.review_violations) if is_agent else [],
        notes=revision.review_notes if is_agent else None,
        created_at=revision.created_at,
    )


def _claim_view(
    claim: Mapping[str, str | None], titles: Mapping[str, str], supported: Mapping[str, KbEntry]
) -> ClaimView:
    ref = claim.get("kb_ref")
    return ClaimView(
        text=claim.get("texto") or "",
        kb_title=None if ref is None else titles.get(ref),
        is_cited=ref is not None,
        is_supported=ref is not None and ref in supported,
    )
