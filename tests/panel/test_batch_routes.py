"""Painel do lote semanal e do feedback: cartoes, acoes de revisao, erros e seguranca."""

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import Response
from panel_rig import LOGIN, PanelRig, Script
from sqlalchemy import event, select

from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.db.models import (
    Approval,
    AuditLog,
    FeedbackExample,
    Job,
    Post,
    PostRevision,
)
from marketing_agent.jobs.queue import GENERATE_POST_JOB
from marketing_agent.kb.service import NewKbEntry

MakePanel = Callable[[Script], PanelRig]
WEEK = "2026-W46"  # segunda 2026-11-09; o relogio dos testes e quinta 2026-11-05
POST_DATE = date(2026, 11, 9)
CAPTION = "Pao quente todas as manhas, feito com receitas de familia."
STALE_MESSAGE = "Recarregue a página"
SECRET = "SEGREDO-NAO-ECOAR"
ACTIONS = ("aprovar", "editar", "alteracoes", "rejeitar")


@dataclass(frozen=True)
class Seeded:
    """Publicacao semeada: o seu id e o da (unica) revisao, se existir."""

    post_id: str
    revision_id: str | None


def lote(week: str = WEEK) -> str:
    return f"/lote/{week}"


def seed_post(
    rig: PanelRig,
    slot: int = 1,
    *,
    lifecycle: str = "in_review",
    scheduled: date = POST_DATE,
    kb_refs: Sequence[str] = (),
    claims: Sequence[dict[str, str | None]] = (),
    caption: str = CAPTION,
    with_revision: bool = True,
    origin: str = "agente",
    verdict: str | None = "aprovar",
    violations: Sequence[str] = (),
    notes: str | None = "Parecer automatico.",
    last_error: str | None = None,
    review_rounds: int = 1,
    brand_id: str | None = None,
    has_job: bool = False,
    theme: str = "Pao do dia",
    hashtags: Sequence[str] = ("#pao", "#padaria"),
) -> Seeded:
    """Publicacao do lote com (ou sem) uma revisao, escrita diretamente na base."""
    brand = brand_id or rig.brand_id
    plan = rig.store.create(brand, ArtifactType.PLANO, {"semana": WEEK, "slots": []})
    strategy = rig.store.create(brand, ArtifactType.ESTRATEGIA, {})
    with rig.factory.begin() as session:
        post = Post(
            brand_id=brand,
            plan_artifact_id=plan.id,
            slot_key=f"{WEEK}-{slot}",
            strategy_artifact_id=strategy.id,
            channels=["instagram"],
            scheduled_date=scheduled,
            brief={
                "tema": theme,
                "pilar": "Producao",
                "objetivo": "Mensagens",
                "kb_refs": list(kb_refs),
            },
            lifecycle=lifecycle,
            review_rounds=review_rounds,
            last_error=last_error,
            created_at=rig.clock.now(),
        )
        session.add(post)
        session.flush()
        revision_id = None
        if with_revision:
            revision = PostRevision(
                brand_id=brand,
                post_id=post.id,
                rev_no=1,
                revision_hash=canonical_revision_hash(
                    caption=caption,
                    hashtags=hashtags,
                    call_to_action="Passe pela loja.",
                    alt_text="Pao artesanal.",
                    claims=claims,
                    channels=["instagram"],
                ),
                caption=caption,
                hashtags=list(hashtags),
                call_to_action="Passe pela loja.",
                alt_text="Pao artesanal.",
                claims=[dict(claim) for claim in claims],
                review_verdict=verdict if origin == "agente" else None,
                review_violations=list(violations) if origin == "agente" else [],
                review_notes=notes if origin == "agente" else None,
                origin=origin,
                created_at=rig.clock.now(),
            )
            session.add(revision)
            session.flush()
            revision_id = revision.id
        if has_job:
            session.add(
                Job(
                    type=GENERATE_POST_JOB,
                    payload={"post_id": post.id, "owner_comment": None, "reason": "plan"},
                    status="queued",
                    run_at=rig.clock.now(),
                    dedupe_key=f"{GENERATE_POST_JOB}:{post.id}:test",
                    created_at=rig.clock.now(),
                    updated_at=rig.clock.now(),
                )
            )
        return Seeded(post.id, revision_id)


def add_kb(
    rig: PanelRig,
    *,
    kind: str = "preco",
    key: str = "pao",
    title: str = "Pao de forno",
    valid_until: date | None = date(2026, 12, 1),
    brand_id: str | None = None,
) -> str:
    is_price = kind == "preco"
    entry = NewKbEntry(
        kind=kind,
        key=key,
        title=title,
        body="Texto do item.",
        price_amount=Decimal("12.50") if is_price else None,
        price_currency="EUR" if is_price else None,
        valid_from=date(2026, 11, 1),
        valid_until=valid_until,
        source_kind="dito_pelo_responsavel",
    )
    return rig.content.kb.add(brand_id or rig.brand_id, entry, "ana").id


def post_row(rig: PanelRig, post_id: str) -> Post:
    with rig.factory() as session:
        return session.get_one(Post, post_id)


def revisions_of(rig: PanelRig, post_id: str) -> list[PostRevision]:
    with rig.factory() as session:
        statement = select(PostRevision).where(PostRevision.post_id == post_id)
        return list(session.scalars(statement.order_by(PostRevision.rev_no)))


def approvals_of(rig: PanelRig, post_id: str) -> list[Approval]:
    with rig.factory() as session:
        statement = select(Approval).where(Approval.post_id == post_id)
        return list(session.scalars(statement.order_by(Approval.decided_at, Approval.id)))


def all_rows(
    rig: PanelRig, model: type[Job] | type[FeedbackExample]
) -> list[Job | FeedbackExample]:
    with rig.factory() as session:
        return list(session.scalars(select(model)))


def edit_form(seeded: Seeded, **overrides: str) -> dict[str, str]:
    assert seeded.revision_id is not None
    form = {
        "base_revision_id": seeded.revision_id,
        "legenda": "Pao novo, quentinho de manha.",
        "hashtags": "#pao #padaria",
        "apelo_a_acao": "Passe pela loja.",
        "alt_text": "Pao artesanal.",
        "feedback": "estilo",
    }
    return {**form, **overrides}


def decision_form(seeded: Seeded, **extra: str) -> dict[str, str]:
    assert seeded.revision_id is not None
    return {"revision_id": seeded.revision_id, **extra}


# ---------------------------------------------------------------- pagina do lote


def test_batch_shows_cards_and_reviewer_verdict(make_panel: MakePanel) -> None:
    rig = make_panel([])
    item_id = add_kb(rig)
    claims = [{"texto": "O pao custa 12,50 euros.", "kb_ref": item_id}]
    seeded = seed_post(
        rig,
        kb_refs=[item_id],
        claims=claims,
        verdict="rever",
        violations=["voz_incoerente", "limite_canal"],
        notes="Tom demasiado formal.",
        review_rounds=2,
    )
    html = rig.get(lote()).text
    assert f'id="post-{seeded.post_id}"' in html
    assert CAPTION in html and "#pao #padaria" in html and "Passe pela loja." in html
    assert "Pao artesanal." in html  # texto alternativo
    assert "O pao custa 12,50 euros." in html and "Origem: Pao de forno" in html
    assert "sem apoio válido" not in html
    assert "Parecer do revisor" in html and "Rever" in html
    assert "voz incoerente" in html and "limite do canal" in html
    assert "Tom demasiado formal." in html
    assert "Voltas do revisor: 2" in html
    assert "Revisão 1" in html and "do agente" in html
    assert "Por rever" in html and "2026-11-09" in html and "Pao do dia" in html
    assert 'name="revision_id"' in html and f'value="{seeded.revision_id}"' in html
    assert f"/post/{seeded.post_id}/aprovar" in html
    assert f"/post/{seeded.post_id}/editar" in html
    assert f"/post/{seeded.post_id}/alteracoes" in html
    assert f"/post/{seeded.post_id}/rejeitar" in html


def test_batch_page_is_a_pure_read(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    for path in (lote(), f"{lote()}/estado"):
        assert rig.get(path).status_code == 200
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"
    assert approvals_of(rig, seeded.post_id) == []
    assert len(revisions_of(rig, seeded.post_id)) == 1


def test_expired_price_item_flagged_on_card(make_panel: MakePanel) -> None:
    """Foco 3: o item valia na revisao mas ja nao vale na data do post."""
    rig = make_panel([])
    expired = add_kb(rig, key="antigo", title="Preco antigo", valid_until=date(2026, 11, 8))
    valid = add_kb(rig, key="novo", title="Preco novo", valid_until=date(2026, 12, 1))
    claims = [
        {"texto": "Pao a preco antigo.", "kb_ref": expired},
        {"texto": "Pao a preco novo.", "kb_ref": valid},
        {"texto": "Feito todos os dias.", "kb_ref": None},
    ]
    seed_post(rig, kb_refs=[expired, valid], claims=claims)
    html = rig.get(lote()).text
    assert "sem apoio válido em 2026-11-09" in html
    assert html.count("sem apoio válido em") == 1
    assert "Preco antigo" in html  # o titulo ajuda a perceber qual item caducou
    assert "Origem: Preco novo" in html
    assert "sem item da base citado" in html  # alegacao sem ref nao e marcada como caducada


def test_claim_outside_the_brief_selection_is_flagged(make_panel: MakePanel) -> None:
    rig = make_panel([])
    item_id = add_kb(rig)
    seed_post(rig, kb_refs=[], claims=[{"texto": "Fora do brief.", "kb_ref": item_id}])
    assert "sem apoio válido em 2026-11-09" in rig.get(lote()).text


def test_deactivated_kb_item_is_flagged(make_panel: MakePanel) -> None:
    rig = make_panel([])
    item_id = add_kb(rig)
    seed_post(rig, kb_refs=[item_id], claims=[{"texto": "Pao.", "kb_ref": item_id}])
    rig.content.kb.deactivate(rig.brand_id, item_id, "ana")
    assert "sem apoio válido em 2026-11-09" in rig.get(lote()).text


def test_owner_edit_shows_no_reviewer_verdict(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seed_post(rig, origin="edicao_responsavel", verdict="rejeitar", notes="Nao deve aparecer.")
    html = rig.get(lote()).text
    assert "Parecer do revisor" not in html and "Nao deve aparecer." not in html
    assert "edição do responsável" in html


def test_cards_ordered_by_date_and_week_scoped(make_panel: MakePanel) -> None:
    rig = make_panel([])
    late = seed_post(rig, 1, scheduled=date(2026, 11, 13), theme="Sexta")
    early = seed_post(rig, 2, scheduled=date(2026, 11, 9), theme="Segunda")
    other_week = rig.get(lote("2026-W47")).text
    assert "Sexta" not in other_week and "Não há publicações nesta semana" in other_week
    html = rig.get(lote()).text
    assert html.index(early.post_id) < html.index(late.post_id)
    assert f'href="{lote("2026-W45")}"' in html and f'href="{lote("2026-W47")}"' in html


def test_week_navigation_crosses_the_iso_year(make_panel: MakePanel) -> None:
    rig = make_panel([])
    html = rig.get(lote("2026-W01")).text
    assert 'href="/lote/2025-W52"' in html
    assert 'href="/lote/2026-W02"' in html


@pytest.mark.parametrize("week", ["2026-W99", "2026-W00", "2026-w46", "amanha", "2026-W4", "<b>"])
def test_invalid_week_is_422(make_panel: MakePanel, week: str) -> None:
    rig = make_panel([])
    for path in (f"/lote/{week}", f"/lote/{week}/estado"):
        response = rig.get(path)
        assert response.status_code in (404, 422), path
        assert "<b>" not in response.text


def test_bare_batch_redirects_to_review_week_then_current_week(make_panel: MakePanel) -> None:
    rig = make_panel([])

    def bare() -> Response:
        return rig.client.get("/lote", headers=rig.headers(), follow_redirects=False)

    response = bare()
    # sem nada por rever, vale a semana do relogio dos testes (quinta 2026-11-05: W45)
    assert response.status_code == 303 and response.headers["location"] == "/lote/2026-W45"
    seed_post(rig)
    assert bare().headers["location"] == lote()


def test_home_links_to_the_review_week(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert "Rever publicações" not in rig.get("/").text
    seed_post(rig, 1)
    html = rig.get("/").text
    assert f'href="{lote()}"' in html and "Rever publicações" in html
    assert 'href="/lote"' in html and 'href="/feedback"' in html  # navegacao


# ---------------------------------------------------------------- estados do cartao


def test_polling_only_while_drafting(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seed_post(rig, 1, lifecycle="in_review")
    quiet = rig.get(lote()).text
    assert "hx-trigger" not in quiet
    assert 'id="lote"' in quiet
    assert "hx-trigger" not in rig.get(f"{lote()}/estado").text

    drafting = seed_post(rig, 2, lifecycle="drafting", with_revision=False, has_job=True)
    planned = seed_post(rig, 3, lifecycle="planned", with_revision=False, has_job=True)
    busy = rig.get(lote()).text
    assert f'hx-get="{lote()}/estado"' in busy and 'hx-trigger="every 5s"' in busy
    assert "Em geração" in busy and "Por gerar" in busy
    partial = rig.get(f"{lote()}/estado", {"HX-Request": "true"})
    assert partial.status_code == 200
    assert 'hx-trigger="every 5s"' in partial.text
    assert "<html" not in partial.text  # fragmento, nao pagina
    for seeded in (drafting, planned):  # sem texto nem acoes enquanto gera
        assert f"/post/{seeded.post_id}/aprovar" not in busy
        assert f"/post/{seeded.post_id}/editar" not in busy

    with rig.factory.begin() as session:  # o trabalho acaba: o polling para sozinho
        for post_id in (drafting.post_id, planned.post_id):
            session.get_one(Post, post_id).lifecycle = "approved"
    assert "hx-trigger" not in rig.get(f"{lote()}/estado").text


def test_failed_drafting_stops_polling_and_shows_the_error(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(
        rig, lifecycle="drafting", with_revision=False, last_error="NeedsHuman", has_job=False
    )
    html = rig.get(lote()).text
    assert "hx-trigger" not in html
    assert "A geração falhou (NeedsHuman)" in html and "Não há texto para editar" in html
    for action in ACTIONS:
        assert f"/post/{seeded.post_id}/{action}" not in html
    assert f'action="/post/{seeded.post_id}/gerar"' in html and "Gerar de novo" in html


def test_failed_drafting_with_revision_offers_only_edit(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle="drafting", last_error="RunLimitExceeded")
    html = rig.get(lote()).text
    assert "A geração falhou (RunLimitExceeded)" in html
    assert f"/post/{seeded.post_id}/editar" in html
    for action in ("aprovar", "alteracoes", "rejeitar"):
        assert f"/post/{seeded.post_id}/{action}" not in html
    assert f"/post/{seeded.post_id}/gerar" in html  # editar a mao ou repetir a geracao


def test_generating_drafting_with_revision_has_no_actions(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle="drafting", has_job=True)
    html = rig.get(lote()).text
    for action in (*ACTIONS, "gerar"):
        assert f"/post/{seeded.post_id}/{action}" not in html


@pytest.mark.parametrize("lifecycle", ["approved", "rejected", "cancelled"])
def test_decided_posts_are_read_only(make_panel: MakePanel, lifecycle: str) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle=lifecycle)
    html = rig.get(lote()).text
    assert CAPTION in html
    for action in ACTIONS:
        assert f"/post/{seeded.post_id}/{action}" not in html
    if lifecycle == "approved":
        assert f"/post/{seeded.post_id}/imagem" in html
        assert html.count("<form") == 1
    else:
        assert "<form" not in html


def test_post_without_revision_shows_no_actions(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle="in_review", with_revision=False)
    html = rig.get(lote()).text
    assert "não tem nenhum texto gerado" in html
    for action in ACTIONS:
        assert f"/post/{seeded.post_id}/{action}" not in html
    # o servico tambem recusa: nao ha revisao sobre a qual decidir
    revision = str(uuid.uuid4())
    assert rig.post(f"/post/{seeded.post_id}/aprovar", {"revision_id": revision}).status_code == 409


def test_active_job_hides_request_changes_but_keeps_edit(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle="in_review", has_job=True)
    html = rig.get(lote()).text
    assert f"/post/{seeded.post_id}/alteracoes" not in html
    assert f"/post/{seeded.post_id}/editar" in html
    response = rig.post(
        f"/post/{seeded.post_id}/alteracoes", decision_form(seeded, comentario="Mais curto.")
    )
    assert response.status_code == 409


def test_feedback_radios_have_no_preselection(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seed_post(rig)
    html = rig.get(lote()).text
    radios = re.findall(r"<input[^>]*type=\"radio\"[^>]*>", html)
    assert radios and not any("checked" in radio for radio in radios)
    edit_values = {m for m in re.findall(r'name="feedback" value="(\w+)"', html)}
    assert edit_values == {"estilo", "correcao_factual", "temporario", "negativo", "nao_usar"}
    reject_html = html[html.index("/rejeitar") :]
    assert 'value="temporario"' not in reject_html  # rejeitar nao tem validade
    assert 'value="nao_usar"' in reject_html


# ---------------------------------------------------------------- acoes


def test_approve_edit_changes_reject_flows(make_panel: MakePanel) -> None:
    rig = make_panel([])
    to_approve = seed_post(rig, 1)
    to_edit = seed_post(rig, 2, has_job=True)
    to_change = seed_post(rig, 3)
    to_reject = seed_post(rig, 4)

    response = rig.post(f"/post/{to_approve.post_id}/aprovar", decision_form(to_approve))
    assert response.status_code == 303
    assert response.headers["location"] == f"{lote()}#post-{to_approve.post_id}"
    assert post_row(rig, to_approve.post_id).lifecycle == "approved"
    [approval] = approvals_of(rig, to_approve.post_id)
    assert (approval.decision, approval.decided_by) == ("approved", LOGIN)
    assert approval.revision_id == to_approve.revision_id
    assert approval.revision_hash == revisions_of(rig, to_approve.post_id)[0].revision_hash

    edit = rig.post(
        f"/post/{to_edit.post_id}/editar",
        edit_form(
            to_edit, legenda="Pao novo.\r\nSegunda linha.", nota="Mais curto", feedback="estilo"
        ),
    )
    assert edit.status_code == 303
    first, second = revisions_of(rig, to_edit.post_id)
    assert (second.rev_no, second.origin) == (2, "edicao_responsavel")
    assert second.caption == "Pao novo.\nSegunda linha."  # CRLF do browser normalizado
    assert second.revision_hash == canonical_revision_hash(
        caption=second.caption,
        hashtags=second.hashtags,
        call_to_action=second.call_to_action,
        alt_text=second.alt_text,
        claims=second.claims,
        channels=["instagram"],
    )
    assert second.revision_hash != first.revision_hash
    assert post_row(rig, to_edit.post_id).lifecycle == "in_review"
    [example] = all_rows(rig, FeedbackExample)
    assert isinstance(example, FeedbackExample)
    assert (example.kind, example.before_text, example.note) == ("estilo", CAPTION, "Mais curto")
    assert example.after_text == "Pao novo.\nSegunda linha." and example.created_by == LOGIN
    assert all(job.status == "cancelled" for job in all_rows(rig, Job) if isinstance(job, Job))

    changes = rig.post(
        f"/post/{to_change.post_id}/alteracoes",
        decision_form(to_change, comentario="  Menos formal, por favor.  "),
    )
    assert changes.status_code == 303
    assert post_row(rig, to_change.post_id).lifecycle == "drafting"
    [requested] = approvals_of(rig, to_change.post_id)
    assert (requested.decision, requested.comment) == (
        "changes_requested",
        "Menos formal, por favor.",
    )
    jobs = [j for j in all_rows(rig, Job) if isinstance(j, Job) and j.status == "queued"]
    [job] = jobs
    assert job.payload == {
        "post_id": to_change.post_id,
        "owner_comment": "Menos formal, por favor.",
        "reason": "changes",
    }

    rejected = rig.post(
        f"/post/{to_reject.post_id}/rejeitar",
        decision_form(to_reject, feedback="negativo", nota="Nao escrever assim"),
    )
    assert rejected.status_code == 303
    assert post_row(rig, to_reject.post_id).lifecycle == "rejected"
    [rejection] = approvals_of(rig, to_reject.post_id)
    assert rejection.decision == "rejected"
    negatives = [
        e
        for e in all_rows(rig, FeedbackExample)
        if isinstance(e, FeedbackExample) and e.kind == "negativo"
    ]
    assert [(e.before_text, e.after_text) for e in negatives] == [(CAPTION, None)]

    with rig.factory() as session:
        actions = sorted(session.scalars(select(AuditLog.action).where(AuditLog.actor == LOGIN)))
    assert {"post.approve", "post.edit", "post.request_changes", "post.reject"} <= set(actions)

    page = rig.get(lote()).text
    assert "Menos formal, por favor." in page  # historico de decisoes no cartao
    assert "Revisão 2" in page and "edição do responsável" in page


def test_after_edit_the_page_offers_the_new_revision(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    assert rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded)).status_code == 303
    _, newest = revisions_of(rig, seeded.post_id)
    html = rig.get(lote()).text
    assert f'value="{newest.id}"' in html and f'value="{seeded.revision_id}"' not in html
    approve = decision_form(Seeded(seeded.post_id, newest.id))
    assert rig.post(f"/post/{seeded.post_id}/aprovar", approve).status_code == 303
    assert post_row(rig, seeded.post_id).lifecycle == "approved"


def test_temporary_feedback_stores_end_of_day_utc(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    form = edit_form(seeded, feedback="temporario", expira_em="2026-12-31")
    assert rig.post(f"/post/{seeded.post_id}/editar", form).status_code == 303
    [example] = all_rows(rig, FeedbackExample)
    assert isinstance(example, FeedbackExample)
    assert example.expires_at == datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)


def test_expiry_date_is_ignored_for_other_kinds(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    form = edit_form(seeded, feedback="estilo", expira_em="2026-12-31")
    assert rig.post(f"/post/{seeded.post_id}/editar", form).status_code == 303
    [example] = all_rows(rig, FeedbackExample)
    assert isinstance(example, FeedbackExample) and example.expires_at is None


def test_nao_usar_saves_no_feedback_but_applies_the_decision(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    response = rig.post(
        f"/post/{seeded.post_id}/rejeitar", decision_form(seeded, feedback="nao_usar")
    )
    assert response.status_code == 303
    assert all_rows(rig, FeedbackExample) == []
    assert post_row(rig, seeded.post_id).lifecycle == "rejected"


def test_actor_comes_from_the_session_not_the_form(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    form = decision_form(seeded, actor="intruso", decided_by="intruso")
    assert rig.post(f"/post/{seeded.post_id}/aprovar", form).status_code == 303
    [approval] = approvals_of(rig, seeded.post_id)
    assert approval.decided_by == LOGIN


def test_edit_requires_feedback_choice(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    for form in (
        {k: v for k, v in edit_form(seeded).items() if k != "feedback"},
        edit_form(seeded, feedback=""),
        edit_form(seeded, feedback="inventado"),
    ):
        response = rig.post(f"/post/{seeded.post_id}/editar", form)
        assert response.status_code == 422
        assert "Escolha o tipo de feedback" in response.text
    reject = rig.post(f"/post/{seeded.post_id}/rejeitar", decision_form(seeded))
    assert reject.status_code == 422 and "Escolha o tipo de feedback" in reject.text
    assert len(revisions_of(rig, seeded.post_id)) == 1
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"
    assert all_rows(rig, FeedbackExample) == [] and approvals_of(rig, seeded.post_id) == []


# ---------------------------------------------------------------- erros


def test_stale_revision_id_is_409(make_panel: MakePanel) -> None:
    """Outra aba editou entretanto: a pagina antiga ainda aponta para a revisao 1."""
    rig = make_panel([])
    seeded = seed_post(rig)
    assert rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded)).status_code == 303
    for action, extra in (
        ("aprovar", {}),
        ("rejeitar", {"feedback": "negativo"}),
        ("alteracoes", {"comentario": "Mudar."}),
    ):
        response = rig.post(f"/post/{seeded.post_id}/{action}", decision_form(seeded, **extra))
        assert response.status_code == 409, action
        assert STALE_MESSAGE in response.text
    stale_edit = rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded, legenda="Outra."))
    assert stale_edit.status_code == 409
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"
    assert approvals_of(rig, seeded.post_id) == []
    assert len(revisions_of(rig, seeded.post_id)) == 2


@pytest.mark.parametrize("lifecycle", ["drafting", "planned", "approved", "rejected", "cancelled"])
def test_actions_on_wrong_state_are_409(make_panel: MakePanel, lifecycle: str) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle=lifecycle, has_job=lifecycle in ("drafting", "planned"))
    forms = {
        "aprovar": decision_form(seeded),
        "alteracoes": decision_form(seeded, comentario="Mudar."),
        "rejeitar": decision_form(seeded, feedback="negativo"),
    }
    for action, form in forms.items():
        response = rig.post(f"/post/{seeded.post_id}/{action}", form)
        assert response.status_code == 409, (lifecycle, action)
        assert STALE_MESSAGE in response.text
    assert post_row(rig, seeded.post_id).lifecycle == lifecycle
    assert approvals_of(rig, seeded.post_id) == []


def test_malformed_revision_id_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    for value in ("", "x", "<script>"):
        response = rig.post(f"/post/{seeded.post_id}/aprovar", {"revision_id": value})
        assert response.status_code == 422 and "<script>" not in response.text
    assert rig.post(f"/post/{seeded.post_id}/aprovar").status_code == 422
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"


@pytest.mark.parametrize("comment", ["", "   ", "\n\t"])
def test_blank_comment_is_422(make_panel: MakePanel, comment: str) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    response = rig.post(
        f"/post/{seeded.post_id}/alteracoes", decision_form(seeded, comentario=comment)
    )
    assert (
        response.status_code == 422
        and "O comentário está vazio ou é demasiado longo" in response.text
    )
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"
    assert approvals_of(rig, seeded.post_id) == []


def test_over_limit_comment_is_422_and_not_echoed(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    limit = rig.settings.max_message_chars
    long = SECRET + "x" * limit
    response = rig.post(
        f"/post/{seeded.post_id}/alteracoes", decision_form(seeded, comentario=long)
    )
    assert response.status_code == 422 and SECRET not in response.text
    exact = rig.post(
        f"/post/{seeded.post_id}/alteracoes", decision_form(seeded, comentario="y" * limit)
    )
    assert exact.status_code == 303


def test_over_limit_edit_field_is_422_without_echo(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    form = edit_form(seeded, alt_text=SECRET + "a" * 600)
    response = rig.post(f"/post/{seeded.post_id}/editar", form)
    assert response.status_code == 422
    assert SECRET not in response.text and "ValidationError" not in response.text
    assert "alt_text" in response.text  # so o campo e o tipo do erro
    assert len(revisions_of(rig, seeded.post_id)) == 1 and all_rows(rig, FeedbackExample) == []


def test_edit_with_empty_caption_or_no_change_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    empty = rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded, legenda="   "))
    assert empty.status_code == 422
    unchanged = rig.post(
        f"/post/{seeded.post_id}/editar",
        edit_form(seeded, legenda=CAPTION, apelo_a_acao="Passe pela loja."),
    )
    assert unchanged.status_code == 422 and "não altera o texto" in unchanged.text
    assert len(revisions_of(rig, seeded.post_id)) == 1


def test_edit_rule_violation_shows_only_field_names(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    bad_tag = rig.post(
        f"/post/{seeded.post_id}/editar", edit_form(seeded, hashtags=f"semcardinal {SECRET}")
    )
    assert bad_tag.status_code == 422 and SECRET not in bad_tag.text
    assert "hashtags[0]" in bad_tag.text
    price = rig.post(
        f"/post/{seeded.post_id}/editar", edit_form(seeded, legenda="Pao a 3,50 euros.")
    )
    assert price.status_code == 422 and "valor monetario" in price.text


def test_edit_blocked_by_expired_claim_support_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    item = add_kb(rig, valid_until=date(2026, 11, 8))
    seeded = seed_post(rig, kb_refs=[item], claims=[{"texto": "Pao.", "kb_ref": item}])
    response = rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded))
    assert response.status_code == 422 and "alegacoes[0].kb_ref" in response.text


def test_temporary_without_or_with_bad_expiry_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    for expira in ("", "amanha", "2026-13-45", "2026-11-01", "2026-11-04", SECRET):
        form = edit_form(seeded, feedback="temporario", expira_em=expira)
        response = rig.post(f"/post/{seeded.post_id}/editar", form)
        assert response.status_code == 422, expira
        assert SECRET not in response.text
    missing = rig.post(f"/post/{seeded.post_id}/editar", edit_form(seeded, feedback="temporario"))
    assert missing.status_code == 422
    assert len(revisions_of(rig, seeded.post_id)) == 1 and all_rows(rig, FeedbackExample) == []
    # o dia de hoje vale ate ao fim do dia (23:59:59 UTC), por isso ainda e futuro
    today = rig.clock.now().date().isoformat()
    ok = rig.post(
        f"/post/{seeded.post_id}/editar", edit_form(seeded, feedback="temporario", expira_em=today)
    )
    assert ok.status_code == 303


def test_reject_with_temporary_feedback_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    response = rig.post(
        f"/post/{seeded.post_id}/rejeitar", decision_form(seeded, feedback="temporario")
    )
    assert response.status_code == 422
    assert post_row(rig, seeded.post_id).lifecycle == "in_review"


def test_over_limit_note_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    response = rig.post(
        f"/post/{seeded.post_id}/rejeitar",
        decision_form(seeded, feedback="negativo", nota=SECRET + "n" * 500),
    )
    assert response.status_code == 422 and SECRET not in response.text


def test_actions_on_other_brand_or_unknown_post_are_404(make_panel: MakePanel) -> None:
    rig = make_panel([])
    other = rig.new_brand()
    foreign = seed_post(rig, brand_id=other)
    own = seed_post(rig, 1)
    forms = {
        "aprovar": decision_form(foreign),
        "editar": edit_form(foreign),
        "alteracoes": decision_form(foreign, comentario="Mudar."),
        "rejeitar": decision_form(foreign, feedback="negativo"),
    }
    for post_id in (foreign.post_id, str(uuid.uuid4())):
        for action, form in forms.items():
            response = rig.post(f"/post/{post_id}/{action}", form)
            assert response.status_code == 404, (post_id, action)
            assert foreign.post_id not in response.text.replace(f"/post/{post_id}", "")
    # mesmo com formulario invalido, nao revela que o id existe noutra marca
    assert rig.post(f"/post/{foreign.post_id}/aprovar", {}).status_code == 404
    assert post_row(rig, foreign.post_id).lifecycle == "in_review"
    assert approvals_of(rig, foreign.post_id) == []
    assert foreign.post_id not in rig.get(lote()).text
    assert own.post_id in rig.get(lote()).text
    assert rig.post("/post/nao-e-um-id/aprovar", {}).status_code == 422


def test_batch_never_lists_other_brand_content(make_panel: MakePanel) -> None:
    rig = make_panel([])
    other = rig.new_brand()
    seed_post(rig, brand_id=other, caption="LEGENDA-ALHEIA", theme="TEMA-ALHEIO")
    seed_post(rig, 1)
    html = rig.get(lote()).text
    assert "LEGENDA-ALHEIA" not in html and "TEMA-ALHEIO" not in html


def test_batch_query_count_does_not_grow_with_posts(make_panel: MakePanel) -> None:
    """Consultas fixas; so a validade da base (por cartao com alegacoes) cresce com os cartoes."""
    rig = make_panel([])
    item = add_kb(rig)
    claims = [{"texto": "Pao.", "kb_ref": item}]
    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    engine = rig.factory.kw["bind"]
    for slot in range(1, 4):
        seed_post(rig, slot)
    event.listen(engine, "before_cursor_execute", record)
    try:
        rig.get(lote())
        plain = len(statements)
        for slot in range(4, 8):
            seed_post(rig, slot, kb_refs=[item], claims=claims)
        statements.clear()
        rig.get(lote())
        with_claims = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert plain == 4  # posts, revisoes, decisoes, trabalhos ativos
    assert with_claims == plain + 1 + 4  # + titulos da base + validade de cada cartao com refs


# ---------------------------------------------------------------- seguranca da pagina


def test_model_and_owner_text_escaped(make_panel: MakePanel) -> None:
    rig = make_panel([])
    payloads = {
        "caption": "<script>alert(1)</script>",
        "tags": "#<b onmouseover=alert(2)>x</b>",
        "claim": "<img src=x onerror=alert(3)>",
        "notes": "<svg onload=alert(4)>",
        "comment": '"><iframe src=//evil.example></iframe>',
        "after": "<script>alert(5)</script>depois",
        "note": "<b onmouseover=alert(6)>nota</b>",
        "title": "<u onclick=alert(7)>titulo</u>",
    }
    item = add_kb(rig, title=payloads["title"])
    claims = [{"texto": payloads["claim"], "kb_ref": item}]
    first = seed_post(
        rig, 1, caption=payloads["caption"], hashtags=[payloads["tags"]], kb_refs=[item],
        claims=claims, notes=payloads["notes"],
    )  # fmt: skip
    second = seed_post(rig, 2, caption="Outra legenda.")
    assert (
        rig.post(
            f"/post/{second.post_id}/alteracoes",
            decision_form(second, comentario=payloads["comment"]),
        ).status_code
        == 303
    )
    edit = edit_form(
        first, legenda=payloads["after"], hashtags="#a", feedback="negativo", nota=payloads["note"]
    )
    assert rig.post(f"/post/{first.post_id}/editar", edit).status_code == 303

    pages = [rig.get(lote()).text, rig.get("/feedback").text]
    raw_markers = (
        "<script>alert",
        "<b onmouseover",
        "<img src=x",
        "<svg onload",
        "<iframe",
        "<u onclick",
    )
    for html in pages:
        for marker in raw_markers:
            assert marker not in html, marker
        assert "|safe" not in html and "<style" not in html
        assert html.count("<script") == html.count('<script src="/static/')
    batch, feedback = pages
    assert "&lt;script&gt;alert(5)&lt;/script&gt;depois" in batch
    assert "&lt;iframe src=//evil.example&gt;&lt;/iframe&gt;" in batch  # comentario no historico
    assert "&lt;b onmouseover=alert(6)&gt;nota&lt;/b&gt;" in feedback
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in feedback  # antes (legenda original)
    assert "&lt;script&gt;alert(5)&lt;/script&gt;depois" in feedback
    # a revisao 1 do agente (alegacao, parecer, titulo) so aparece com a edicao em historico:
    # valida-se tambem num cartao ainda por decidir
    third = seed_post(
        rig, 3, kb_refs=[item], claims=claims, notes=payloads["notes"], caption=payloads["caption"],
        hashtags=[payloads["tags"]],
    )  # fmt: skip
    html = rig.get(lote()).text
    assert third.post_id in html
    for marker in raw_markers:
        assert marker not in html, marker
    for escaped in (
        "&lt;img src=x onerror=alert(3)&gt;",
        "&lt;svg onload=alert(4)&gt;",
        "&lt;u onclick=alert(7)&gt;",
    ):
        assert escaped in html


def test_pages_have_no_inline_script_forms_have_csrf(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seed_post(rig, 1)
    seed_post(rig, 2, lifecycle="drafting", with_revision=False, has_job=True)
    for path in (lote(), f"{lote()}/estado", "/feedback"):
        html = rig.get(path).text
        assert html.count("<script") == html.count('<script src="/static/')
        assert "<style" not in html and ' style="' not in html
    html = rig.get(lote()).text
    assert html.count("<form") == html.count('name="csrf_token"') == 5
    assert rig.token() in html


def test_panel_javascript_guards_polling_swap() -> None:
    from marketing_agent.panel.rendering import STATIC_DIR

    script = (STATIC_DIR / "panel.js").read_text(encoding="utf-8")
    assert "htmx:beforeSwap" in script and "lote" in script


# ---------------------------------------------------------------- feedback


def seed_feedback(rig: PanelRig, seeded: Seeded, kind: str, **fields: object) -> str:
    assert seeded.revision_id is not None
    values = {
        "before_text": "Antes",
        "after_text": "Depois",
        "note": "Nota",
        "expires_at": None,
        **fields,
    }
    with rig.factory.begin() as session:
        row = FeedbackExample(
            brand_id=rig.brand_id,
            post_id=seeded.post_id,
            revision_id=seeded.revision_id,
            kind=kind,
            created_by="ana",
            created_at=rig.clock.now(),
            **values,  # type: ignore[arg-type]
        )
        session.add(row)
        session.flush()
        return row.id


def test_feedback_page_and_deactivate(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert "Ainda não há feedback guardado" in rig.get("/feedback").text
    seeded = seed_post(rig)
    style = seed_feedback(
        rig, seeded, "estilo", before_text="ANTES-1", after_text="DEPOIS-1", note="NOTA-1"
    )
    temp = seed_feedback(
        rig,
        seeded,
        "temporario",
        before_text="ANTES-2",
        expires_at=rig.clock.now() + timedelta(days=3),
    )
    expired_id = seed_feedback(
        rig,
        seeded,
        "negativo",
        before_text="ANTES-3",
        expires_at=rig.clock.now() - timedelta(days=1),
    )
    html = rig.get("/feedback").text
    for text in ("ANTES-1", "DEPOIS-1", "NOTA-1", "ANTES-2", "ANTES-3"):
        assert text in html
    assert "Estilo" in html and "Temporário" in html and "Negativo" in html
    assert "2026-11-08 12:00 UTC" in html  # validade do temporario
    assert "Expirado" in html and "Ativo" in html
    for example_id in (style, temp, expired_id):
        assert f"/feedback/{example_id}/desativar" in html

    response = rig.post(f"/feedback/{style}/desativar")
    assert response.status_code == 303 and response.headers["location"] == "/feedback"
    with rig.factory() as session:
        row = session.get_one(FeedbackExample, style)
        assert not row.is_active and row.before_text == "ANTES-1"  # nunca se apaga
        actions = list(session.scalars(select(AuditLog.action).where(AuditLog.actor == LOGIN)))
    assert actions.count("feedback.deactivate") == 1
    after = rig.get("/feedback").text
    assert "ANTES-1" in after and "Desativado" in after
    assert f"/feedback/{style}/desativar" not in after
    assert rig.post(f"/feedback/{style}/desativar").status_code == 303  # idempotente
    with rig.factory() as session:
        again = list(session.scalars(select(AuditLog.action).where(AuditLog.actor == LOGIN)))
    assert again.count("feedback.deactivate") == 1


def test_deactivate_unknown_or_foreign_feedback_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    other = rig.new_brand()
    foreign_post = seed_post(rig, brand_id=other)
    with rig.factory.begin() as session:
        row = FeedbackExample(
            brand_id=other,
            post_id=foreign_post.post_id,
            revision_id=foreign_post.revision_id,
            kind="estilo",
            before_text="FEEDBACK-ALHEIO",
            created_by="ana",
            created_at=rig.clock.now(),
        )
        session.add(row)
        session.flush()
        foreign_id = row.id
    for example_id in (foreign_id, str(uuid.uuid4())):
        response = rig.post(f"/feedback/{example_id}/desativar")
        assert response.status_code == 422
        assert foreign_id not in response.text
    assert rig.post("/feedback/nao-e-um-id/desativar").status_code == 422
    with rig.factory() as session:
        assert session.get_one(FeedbackExample, foreign_id).is_active
    assert "FEEDBACK-ALHEIO" not in rig.get("/feedback").text


def test_feedback_page_is_bounded(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig)
    for number in range(103):
        seed_feedback(rig, seeded, "estilo", before_text=f"B{number:03d}")
    html = rig.get("/feedback").text
    assert html.count('id="feedback-') == 100


# ---------------------------------------------------------------- repetir a geracao


def jobs_of(rig: PanelRig, post_id: str) -> list[Job]:
    with rig.factory() as session:
        return [j for j in session.scalars(select(Job)) if j.payload.get("post_id") == post_id]


def test_retry_button_rules(make_panel: MakePanel) -> None:
    rig = make_panel([])
    shown = {
        "failed_no_text": seed_post(
            rig, 1, lifecycle="drafting", with_revision=False, last_error="NeedsHuman"
        ),
        "failed_with_text": seed_post(rig, 2, lifecycle="drafting", last_error="RunLimitExceeded"),
        # planned com o trabalho morto e sem motivo registado: sem botao ficava sem saida
        "planned_dead_job": seed_post(
            rig, 3, lifecycle="planned", with_revision=False, last_error=None
        ),
    }
    hidden = {
        "drafting_running": seed_post(rig, 4, lifecycle="drafting", has_job=True),
        "planned_with_job": seed_post(
            rig, 5, lifecycle="planned", with_revision=False, has_job=True
        ),
        "failed_but_job_active": seed_post(
            rig, 6, lifecycle="drafting", last_error="NeedsHuman", has_job=True
        ),
        "drafting_no_error_no_job": seed_post(rig, 7, lifecycle="drafting", with_revision=False),
        "in_review": seed_post(rig, 8, lifecycle="in_review"),
    }
    more = {
        name: seed_post(rig, 9 + i, lifecycle=name)
        for i, name in enumerate(("approved", "rejected", "cancelled"))
    }
    html = rig.get(lote()).text
    for name, seeded in shown.items():
        assert f'action="/post/{seeded.post_id}/gerar"' in html, name
    for name, seeded in {**hidden, **more}.items():
        assert f"/post/{seeded.post_id}/gerar" not in html, name
    assert html.count("Gerar de novo") == len(shown)
    assert "Não há texto para editar" in html  # a explicacao continua, agora com a saida ao lado


def test_planned_post_without_job_stops_polling(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seed_post(rig, lifecycle="planned", with_revision=False)
    assert "hx-trigger" not in rig.get(lote()).text


def test_retry_route_enqueues_one_job_and_redirects(make_panel: MakePanel) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle="drafting", with_revision=False, last_error="NeedsHuman")

    response = rig.post(f"/post/{seeded.post_id}/gerar", {"actor": "intruso"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/lote/{WEEK}#post-{seeded.post_id}"
    [job] = jobs_of(rig, seeded.post_id)
    assert (job.status, job.payload["reason"], job.payload["owner_comment"]) == (
        "queued",
        "plan",
        None,
    )
    assert post_row(rig, seeded.post_id).last_error is None
    with rig.factory() as session:
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == "post.retry_generation")
        ).one()
    assert (audit.actor, audit.subject_id) == (LOGIN, seeded.post_id)
    page = rig.get(lote()).text
    assert f"/post/{seeded.post_id}/gerar" not in page  # ja ha trabalho: sem botao e com polling
    assert 'hx-trigger="every 5s"' in page
    # segundo clique (pagina antiga): 409 e continua a haver um so trabalho
    again = rig.post(f"/post/{seeded.post_id}/gerar")
    assert again.status_code == 409 and STALE_MESSAGE in again.text
    assert len(jobs_of(rig, seeded.post_id)) == 1


@pytest.mark.parametrize("lifecycle", ["in_review", "approved", "rejected", "cancelled"])
def test_retry_route_refused_in_other_states_is_409(make_panel: MakePanel, lifecycle: str) -> None:
    rig = make_panel([])
    seeded = seed_post(rig, lifecycle=lifecycle)
    response = rig.post(f"/post/{seeded.post_id}/gerar")
    assert response.status_code == 409 and STALE_MESSAGE in response.text
    assert jobs_of(rig, seeded.post_id) == []
    assert post_row(rig, seeded.post_id).lifecycle == lifecycle


def test_retry_route_on_other_brand_or_unknown_post_is_404(make_panel: MakePanel) -> None:
    rig = make_panel([])
    foreign = seed_post(rig, brand_id=rig.new_brand(), lifecycle="planned", with_revision=False)
    for post_id in (foreign.post_id, str(uuid.uuid4())):
        response = rig.post(f"/post/{post_id}/gerar")
        assert response.status_code == 404, post_id
        assert foreign.post_id not in response.text.replace(f"/post/{post_id}", "")
    assert jobs_of(rig, foreign.post_id) == []
    assert rig.post("/post/nao-e-um-id/gerar").status_code == 422
