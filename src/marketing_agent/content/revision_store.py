"""Estado do post durante uma geracao: bloqueio, deteccao de obsoletos e gravacao da revisao.

Regra central (Foco 1): o pipeline demora minutos e corre fora de qualquer transacao; so a
gravacao final e atomica. Antes de gravar, o post e bloqueado (`SELECT ... FOR UPDATE`) e
comparado com o `PostSnapshot` tirado no arranque do job. Ordem dos locks: SEMPRE o post antes
do job (a aprovacao de um plano bloqueia posts -> jobs; a ordem contraria daria impasse).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.db.models import JsonObject, Post, PostRevision

DRAFTING = "drafting"
CANCELLED = "cancelled"
AGENT_ORIGIN = "agente"

StaleReason = Literal["cancelled", "not_drafting", "newer_revision", "content_changed"]


@dataclass(frozen=True)
class PostSnapshot:
    """O que o job viu ao arrancar: com isto se decide, no fim, se a gravacao ainda vale."""

    post_id: str
    brand_id: str
    scheduled_date: date
    channels: tuple[str, ...]
    brief: JsonObject
    last_rev_no: int


@dataclass(frozen=True)
class GeneratedText:
    """Resultado de uma geracao: o rascunho final, o parecer efetivo e as voltas gastas."""

    draft: RascunhoTexto
    verdict: str
    violations: list[str]
    notes: str
    rounds: int
    run_id: str


def canonical_revision_hash(
    *,
    caption: str,
    hashtags: Sequence[str],
    call_to_action: str | None,
    alt_text: str | None,
    claims: Sequence[Mapping[str, str | None]],
    channels: Sequence[str],
) -> str:
    """SHA-256 do JSON de chaves ordenadas do texto e dos canais (a ordem dos canais nao conta).

    Partilhado por quem cria revisoes (o pipeline e a edicao do responsavel): so assim o hash
    de uma aprovacao identifica o mesmo texto, venha de onde vier.
    """
    body = {
        "legenda": caption,
        "hashtags": list(hashtags),
        "apelo": call_to_action,
        "alt_text": alt_text,
        "alegacoes": [dict(claim) for claim in claims],
        "canais": sorted(channels),
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def lock_post(session: Session, post_id: str) -> Post | None:
    """Le o post com `FOR UPDATE`; o lock dura ate ao fim da transacao do chamador."""
    return session.scalars(select(Post).where(Post.id == post_id).with_for_update()).one_or_none()


def _last_rev_no(session: Session, post_id: str) -> int:
    newest = select(func.max(PostRevision.rev_no)).where(PostRevision.post_id == post_id)
    return session.scalar(newest) or 0


def take_snapshot(session: Session, post: Post) -> PostSnapshot:
    """Fotografia do post para a verificacao de obsoletos; chamar com o post bloqueado."""
    return PostSnapshot(
        post_id=post.id,
        brand_id=post.brand_id,
        scheduled_date=post.scheduled_date,
        channels=tuple(post.channels),
        brief=dict(post.brief),
        last_rev_no=_last_rev_no(session, post.id),
    )


def stale_reason(session: Session, post: Post, snapshot: PostSnapshot) -> StaleReason | None:
    """Porque a gravacao deixou de valer (`None` = o post continua como o job o viu).

    Chamar com o post bloqueado. Obsoleto quando:
    - o post foi `cancelled` (o slot saiu do plano);
    - o post ja nao esta `drafting` (aprovado, rejeitado ou reposto a `planned` por um plano novo);
    - existe outra revisao alem das que o job viu (edicao do responsavel ou de outro job);
    - o conteudo de que a geracao partiu mudou: `brief`, `channels` ou `scheduled_date`.
    Os ponteiros `plan_artifact_id` e `strategy_artifact_id` NAO contam: aprovar de novo um
    plano identico repoe-nos nos posts mantidos sem mudar nada do que foi escrito.
    """
    if post.lifecycle == CANCELLED:
        return "cancelled"
    if post.lifecycle != DRAFTING:
        return "not_drafting"
    if _last_rev_no(session, post.id) != snapshot.last_rev_no:
        return "newer_revision"
    content = (dict(post.brief), tuple(post.channels), post.scheduled_date)
    if content != (snapshot.brief, snapshot.channels, snapshot.scheduled_date):
        return "content_changed"
    return None


def _claims(draft: RascunhoTexto) -> list[dict[str, str | None]]:
    return [{"texto": claim.texto, "kb_ref": claim.kb_ref} for claim in draft.alegacoes]


def save_generated(
    session: Session, post: Post, generated: GeneratedText, snapshot: PostSnapshot, now: datetime
) -> None:
    """Grava a revisao do agente e passa o post a `in_review`, na transacao do chamador.

    `rev_no` e o seguinte ao que o job viu; como o post esta bloqueado e nao e obsoleto, nenhuma
    outra revisao pode ter aparecido (e `UNIQUE (post_id, rev_no)` e a rede de seguranca).
    """
    draft, claims = generated.draft, _claims(generated.draft)
    revision_hash = canonical_revision_hash(
        caption=draft.legenda,
        hashtags=draft.hashtags,
        call_to_action=draft.apelo_a_acao,
        alt_text=draft.alt_text,
        claims=claims,
        channels=post.channels,
    )
    session.add(
        PostRevision(
            brand_id=post.brand_id,
            post_id=post.id,
            rev_no=snapshot.last_rev_no + 1,
            revision_hash=revision_hash,
            caption=draft.legenda,
            hashtags=list(draft.hashtags),
            call_to_action=draft.apelo_a_acao,
            alt_text=draft.alt_text,
            claims=claims,
            review_verdict=generated.verdict,
            review_violations=generated.violations,
            review_notes=generated.notes,
            origin=AGENT_ORIGIN,
            produced_by_run_id=generated.run_id,
            created_at=now,
        )
    )
    post.lifecycle = "in_review"
    post.review_rounds = generated.rounds
    post.last_error = None
