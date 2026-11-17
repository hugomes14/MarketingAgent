"""Plano semanal de texto de ponta a ponta: plano, worker, lote, revisao e feedback pelo painel.

Percorre o fluxo real com o painel HTTP, os servicos reais, o `Worker` e o pipeline sobre um
cliente LLM falso (cenarios `weekly_plan`, `caption_draft` e `review`). O relogio de teste e
fixo, por isso a ordem em que o worker reclama os jobs de um mesmo lote nao e garantida: nada
aqui depende de que publicacao e gerada primeiro.
"""

import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from image_factory import make_png
from panel_rig import LOGIN, PanelRig, Script
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.clock import FixedClock
from marketing_agent.content.pipeline import ContentPipeline
from marketing_agent.db.models import (
    AgentRun,
    Approval,
    AuditLog,
    CostEntry,
    FeedbackExample,
    Job,
    Post,
    PostRevision,
)
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.jobs.worker import Worker
from marketing_agent.kb.service import KbService, NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import LlmResponse, TextPart
from marketing_agent.media.brand_kit import BrandKitInput
from marketing_agent.startup import content_job_lease

Factory = sessionmaker[Session]
WEEK = "2026-W46"  # segunda 2026-11-09; o relogio dos testes e quinta 2026-11-05 (semana W45)
EDITED_CAPTION = "ESTILO-NOVO: pao quentinho de manha a 12,50 EUR."
OWNER_COMMENT = "Menos formal e mais curto, por favor."
POLLING = "hx-trigger"


class WeeklyRig:
    """Painel, worker e pipeline sobre a mesma base e o mesmo cliente LLM falso."""

    def __init__(self, panel: PanelRig) -> None:
        self.panel = panel
        queue = JobQueue(panel.factory, panel.clock, content_job_lease(time_multiplier=1))
        pipeline = ContentPipeline(
            panel.factory,
            panel.engine,
            panel.content.kb,
            panel.content.feedback,
            queue,
            panel.clock,
            panel.store,
            panel.strategy,
            panel.media.library,
            panel.media.kits,
            "medium",
        )
        self.worker = Worker(queue, pipeline, "worker-e2e")

    def drain(self) -> int:
        """`run_once` ate a fila esvaziar; devolve quantos jobs processou."""
        processed = 0
        while self.worker.run_once():
            processed += 1
            assert processed < 20, "a fila nao esvazia"
        return processed

    def propose_and_approve_plan(self) -> str:
        plan_id = self.panel.propose_plan()
        response = self.panel.post(f"/plano/{plan_id}/aprovar")
        assert response.status_code == 303
        assert response.headers["location"] == f"/lote/{WEEK}"
        return plan_id

    def batch(self) -> str:
        return self.panel.get(f"/lote/{WEEK}").text

    def state(self) -> str:
        return self.panel.get(f"/lote/{WEEK}/estado", {"HX-Request": "true"}).text

    def posts(self) -> list[Post]:
        with self.panel.factory() as session:
            return list(session.scalars(select(Post).order_by(Post.slot_key)))

    def revisions(self, post_id: str) -> list[PostRevision]:
        with self.panel.factory() as session:
            statement = select(PostRevision).where(PostRevision.post_id == post_id)
            return list(session.scalars(statement.order_by(PostRevision.rev_no)))

    def rows(self, model: type[Job] | type[PostRevision] | type[Approval]) -> int:
        with self.panel.factory() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def copywriter_requests(self) -> list[str]:
        """Texto dos pedidos do Copywriter (os unicos com `<exemplos_estilo>`), por ordem."""
        texts = [
            part.text
            for request in self.panel.llm.requests
            for part in request.messages[0].content
            if isinstance(part, TextPart)
        ]
        return [item for item in texts if "<exemplos_estilo>" in item]

    def action(self, post: Post, name: str, data: dict[str, str]) -> int:
        return self.panel.post(f"/post/{post.id}/{name}", data).status_code


def plan_citing(price_id: str) -> LlmResponse:
    """Plano valido de 3 publicacoes (11-09, 11-11 e 11-13) que escolhem o item de preco."""
    [response] = fake_scenarios.weekly_plan(WEEK, 3)
    assert isinstance(response, LlmResponse)
    body = json.loads(response.text)
    for slot in body["slots"]:
        slot["kb_refs"] = [price_id]
    return LlmResponse(
        text=json.dumps(body),
        usage=response.usage,
        provider_request_id=None,
        resolved_model=response.resolved_model,
        stop_reason=response.stop_reason,
    )


def text_pass(price_id: str, label: str) -> Script:
    """Uma passagem do pacote: Copywriter (com alegacao do preco), Designer e Revisor que aprova."""
    claim = {"texto": "Pao a 12,50 EUR.", "kb_ref": price_id}
    return [
        *fake_scenarios.caption_draft(f"Pao quente {label} a 12,50 EUR.", alegacoes=[claim]),
        *fake_scenarios.visual_draft(),
        *fake_scenarios.review("aprovar", notas=f"Parecer {label}.", imagem_verificada=True),
    ]


MakeWeekly = Callable[[int], WeeklyRig]


@pytest.fixture
def make_weekly(
    make_panel: Callable[..., PanelRig], session_factory: Factory, clock: FixedClock, brand_id: str
) -> MakeWeekly:
    """Monta o lote semanal com `passes` passagens de texto no guiao do LLM falso."""

    def make(passes: int) -> WeeklyRig:
        kb = KbService(session_factory, ArtifactStore(session_factory, clock), clock)
        entry = NewKbEntry(
            kind="preco",
            key="pao",
            title="Pao de forno a lenha",
            body="Pao de forno a lenha.",
            price_amount=Decimal("12.50"),
            price_currency="EUR",
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 12, 1),
            source_kind="dito_pelo_responsavel",
        )
        price_id = kb.add(brand_id, entry, "ana").id
        script: Script = [
            *fake_scenarios.interview_full(3),
            *fake_scenarios.diagnosis_and_strategy(),
            plan_citing(price_id),
        ]
        for label in "ABCDEF"[:passes]:
            script.extend(text_pass(price_id, label))
        panel = make_panel(script)
        panel.approve_strategy()
        kit = BrandKitInput(cor_primaria="#1F3A5F", cor_secundaria="#FFFFFF", fonte="inter-bold")
        panel.media.kits.save(brand_id, kit, make_png(240, 240, alpha=True), "ana")
        return WeeklyRig(panel)

    return make


def edit_form(revision_id: str, **overrides: str) -> dict[str, str]:
    form = {
        "base_revision_id": revision_id,
        "legenda": EDITED_CAPTION,
        "hashtags": "#pao #padaria",
        "apelo_a_acao": "Passe pela loja.",
        "alt_text": "Pao artesanal.",
        "feedback": "estilo",
        "nota": "Mais curto",
    }
    return {**form, **overrides}


def test_weekly_text_flow(make_weekly: MakeWeekly) -> None:
    rig = make_weekly(4)  # 3 publicacoes + 1 regeneracao pedida pelo responsavel
    panel = rig.panel
    plan_id = rig.propose_and_approve_plan()
    assert rig.rows(Job) == 3 and {p.lifecycle for p in rig.posts()} == {"planned"}
    assert POLLING in rig.state()  # ha publicacoes por gerar: a pagina vai perguntando

    # --- o worker gera os tres textos e a fila esvazia
    assert rig.drain() == 3
    assert not rig.worker.run_once()
    first, second, third = rig.posts()
    assert {p.lifecycle for p in rig.posts()} == {"in_review"}
    for post in rig.posts():
        [revision] = rig.revisions(post.id)
        assert (revision.rev_no, revision.origin, revision.review_verdict) == (
            1,
            "agente",
            "aprovar",
        )
        assert [claim["kb_ref"] for claim in revision.claims] == [
            kb_ref for kb_ref in post.brief["kb_refs"]
        ]
    assert POLLING not in rig.state() and POLLING not in rig.batch()
    assert "sem apoio válido" not in rig.batch()
    assert len(rig.copywriter_requests()) == 3
    assert not any("ESTILO-NOVO" in item for item in rig.copywriter_requests())

    # --- decisoes: aprovar a primeira, editar a segunda (estilo), alteracoes na terceira
    [first_rev] = rig.revisions(first.id)
    [second_rev] = rig.revisions(second.id)
    [third_rev] = rig.revisions(third.id)
    assert rig.action(first, "aprovar", {"revision_id": first_rev.id}) == 303
    assert rig.action(second, "editar", edit_form(second_rev.id)) == 303
    assert (
        rig.action(third, "alteracoes", {"revision_id": third_rev.id, "comentario": OWNER_COMMENT})
        == 303
    )
    assert POLLING in rig.state()  # a terceira voltou a gerar
    lifecycles = {p.slot_key: p.lifecycle for p in rig.posts()}
    assert list(lifecycles.values()) == ["approved", "in_review", "drafting"]

    # a edicao e a revisao mais recente: aprovar a antiga e recusado (409), sem efeitos
    second_revs = rig.revisions(second.id)
    assert [(r.rev_no, r.origin) for r in second_revs] == [(1, "agente"), (2, "edicao_responsavel")]
    assert rig.action(second, "aprovar", {"revision_id": second_rev.id}) == 409
    assert rig.rows(Approval) == 2  # so a aprovacao da primeira e o pedido de alteracoes

    # --- o worker corre outra vez: so o job das alteracoes
    assert rig.drain() == 1
    requests = rig.copywriter_requests()
    assert len(requests) == 4
    style_block = re.search(r"<exemplos_estilo>(.*?)</exemplos_estilo>", requests[3], re.DOTALL)
    assert style_block is not None and EDITED_CAPTION in style_block.group(1)
    assert "Pao quente" in style_block.group(1)  # o "antes" do mesmo par
    assert not any("ESTILO-NOVO" in item for item in requests[:3])
    comment_block = re.search(
        r"<comentario_responsavel>(.*?)</comentario_responsavel>", requests[3], re.DOTALL
    )
    assert comment_block is not None and OWNER_COMMENT in comment_block.group(1)
    with panel.factory() as session:
        changes_jobs = session.scalars(
            select(Job).where(Job.payload["reason"].astext == "changes")
        ).all()
    [changes_job] = changes_jobs
    assert changes_job.payload["owner_comment"] == OWNER_COMMENT
    assert changes_job.payload["post_id"] == third.id and changes_job.status == "succeeded"

    third_revs = rig.revisions(third.id)
    assert [(r.rev_no, r.origin) for r in third_revs] == [(1, "agente"), (2, "agente")]
    assert {p.slot_key: p.lifecycle for p in rig.posts()}[third.slot_key] == "in_review"
    assert POLLING not in rig.state() and POLLING not in rig.batch()
    assert rig.action(third, "aprovar", {"revision_id": third_rev.id}) == 409  # revisao antiga

    # --- a aprovacao vale so para a revisao mais recente (a edicao do responsavel)
    assert rig.action(second, "aprovar", {"revision_id": second_revs[-1].id}) == 303
    with panel.factory() as session:
        approvals = session.scalars(select(Approval).order_by(Approval.decided_at)).all()
    by_post = {a.post_id: a for a in approvals if a.decision == "approved"}
    assert by_post[second.id].revision_id == second_revs[-1].id
    assert by_post[second.id].revision_hash == second_revs[-1].revision_hash
    assert by_post[first.id].revision_id == first_rev.id
    assert rig.rows(Approval) == 3

    # --- custos: todas as execucoes do texto (Copywriter e Revisor) tem cost_entry
    with panel.factory() as session:
        runs = session.scalars(
            select(AgentRun).where(AgentRun.role.in_(("copywriter", "revisor")))
        ).all()
        roles = sorted(run.role for run in runs)
        assert roles == ["copywriter"] * 4 + ["revisor"] * 4
        for run in runs:
            entries = session.scalars(select(CostEntry).where(CostEntry.run_id == run.id)).all()
            assert entries, f"execucao {run.role} {run.id} sem cost_entry"
        agent_revisions = session.scalars(
            select(PostRevision).where(PostRevision.origin == "agente")
        ).all()
        assert len(agent_revisions) == 4
        for revision in agent_revisions:
            assert revision.produced_by_run_id is not None
            run = session.get_one(AgentRun, revision.produced_by_run_id)
            assert run.role == "copywriter"
            assert session.scalar(
                select(func.count()).select_from(CostEntry).where(CostEntry.run_id == run.id)
            )
        [owner_revision] = session.scalars(
            select(PostRevision).where(PostRevision.origin == "edicao_responsavel")
        ).all()
        assert owner_revision.produced_by_run_id is None  # nenhum LLM produziu esta revisao

    # --- feedback e auditoria: o exemplo de estilo e o login da sessao
    with panel.factory() as session:
        [example] = session.scalars(select(FeedbackExample)).all()
        assert (example.kind, example.note, example.after_text) == (
            "estilo",
            "Mais curto",
            EDITED_CAPTION,
        )
        assert example.before_text == second_rev.caption and example.created_by == LOGIN
        audit = session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(
                    (
                        "plan.approve",
                        "post.approve",
                        "post.edit",
                        "post.request_changes",
                        "feedback.record",
                    )
                )
            )
        ).all()
    assert sorted(entry.action for entry in audit) == [
        "feedback.record",
        "plan.approve",
        "post.approve",
        "post.approve",
        "post.edit",
        "post.request_changes",
    ]
    assert {entry.actor for entry in audit} == {LOGIN}
    assert panel.store.get(plan_id).payload["semana"] == WEEK

    # --- exatamente uma vez: o worker, a correr de novo, nao cria nada nem chama o LLM
    requests_before = len(panel.llm.requests)
    assert rig.drain() == 0
    assert (rig.rows(Job), rig.rows(PostRevision), len(panel.llm.requests)) == (
        4,
        3 + 1 + 1,  # 3 geradas + 1 edicao do responsavel + 1 regeneracao
        requests_before,
    )
    with panel.factory() as session:
        statuses = session.scalars(select(Job.status)).all()
        assert statuses == ["succeeded"] * 4
        duplicates = session.execute(
            text("select post_id, rev_no from post_revision group by 1, 2 having count(*) > 1")
        ).all()
        assert duplicates == []


def test_expired_price_is_flagged_on_the_cards_dated_after_it(make_weekly: MakeWeekly) -> None:
    """Foco 3: o preco citado valia quando o texto foi gerado e caducou antes da data do post."""
    rig = make_weekly(3)
    rig.propose_and_approve_plan()
    assert rig.drain() == 3
    assert "sem apoio válido" not in rig.batch()

    # O preco passa a valer so ate 2026-11-10: as publicacoes de 11-11 e 11-13 perdem o apoio.
    with rig.panel.factory.begin() as session:
        session.execute(text("update kb_item set valid_until = date '2026-11-10'"))
    html = rig.batch()
    assert html.count("sem apoio válido em") == 2
    assert "sem apoio válido em 2026-11-11" in html and "sem apoio válido em 2026-11-13" in html
    assert "sem apoio válido em 2026-11-09" not in html
    assert "Pao de forno a lenha" in html
