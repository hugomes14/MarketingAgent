"""Painel da base de conhecimento e do plano semanal: autenticacao, CSRF, erros e fluxos."""

import re
import uuid
from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from panel_rig import LOGIN, PanelRig, Script, json_response
from sqlalchemy import func, select, update
from starlette.routing import BaseRoute, Mount, Route

from marketing_agent.artifacts.store import ArtifactState, ArtifactType
from marketing_agent.db.models import AuditLog, Job, KbItem, Post
from marketing_agent.kb.service import NewKbEntry
from marketing_agent.llm import fake_scenarios
from marketing_agent.panel import (
    routes_batch,
    routes_feedback,
    routes_kb,
    routes_media,
    routes_plan,
)
from marketing_agent.proposals import ProposalInProgress

MakePanel = Callable[..., PanelRig]
WEEK = "2026-W46"  # o relogio dos testes e quinta 2026-11-05: a semana seguinte e a W46
UPSTREAM_MESSAGE = "Não foi possível gerar o resultado. Tente novamente mais tarde."
MODEL_SECRET = "SEGREDO-DO-MODELO"
PRICE_FORM = {
    "kind": "preco",
    "key": "bolo-aniversario",
    "title": "Bolo de aniversário",
    "body": "Bolo para 10 pessoas.",
    "price_amount": "12.50",
    "price_currency": "EUR",
    "valid_from": "2026-11-01",
    "valid_until": "2026-12-01",
    "source_kind": "verificado",
}


def planning_script(slots: int = 3, week: str = WEEK) -> Script:
    return [
        *fake_scenarios.interview_full(3),
        *fake_scenarios.diagnosis_and_strategy(),
        *fake_scenarios.weekly_plan(week, slots),
    ]


def planning_rig(make_panel: MakePanel, slots: int = 3) -> PanelRig:
    rig = make_panel(planning_script(slots))
    rig.approve_strategy()
    return rig


def kb_items(rig: PanelRig) -> list[KbItem]:
    with rig.factory() as session:
        return list(session.scalars(select(KbItem).order_by(KbItem.key)))


def count_rows(rig: PanelRig, model: type[Post] | type[Job]) -> int:
    with rig.factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def add_item(rig: PanelRig, title: str = "Oferta de outono", key: str = "outono") -> str:
    entry = NewKbEntry(
        kind="oferta",
        key=key,
        title=title,
        body="Texto da oferta.",
        valid_from=date(2026, 11, 1),
        source_kind="dito_pelo_responsavel",
    )
    return rig.content.kb.add(rig.brand_id, entry, "ana").id


# ---------------------------------------------------------------- auth, CSRF e Host


# Rotas servidas que nao entram na verificacao por-rota: so montagens de ficheiros estaticos.
# Qualquer outra montagem (sub-app) ou rota fora do esquema OpenAPI faz o teste falhar.
ALLOWED_MOUNTS = frozenset({"/static"})
Endpoint = tuple[str, str]


def openapi_endpoints(app: FastAPI) -> set[Endpoint]:
    """(metodo, caminho) das rotas que o esquema OpenAPI gerado pela app declara."""
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }


def route_table_endpoints(routes: Iterable[BaseRoute], prefix: str = "") -> set[Endpoint]:
    """(metodo, caminho) lidos da tabela real de rotas, incluindo as `include_in_schema=False`.

    Percorre as rotas simples e os routers incluidos (`_IncludedRouter.original_router`). Uma
    montagem (`Mount`) fora de `ALLOWED_MOUNTS` falha ruidosamente: nao se sabe o que serve.
    """
    found: set[Endpoint] = set()
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            context_prefix = getattr(getattr(route, "include_context", None), "prefix", "")
            found |= route_table_endpoints(original.routes, prefix + (context_prefix or ""))
        elif isinstance(route, Mount):
            assert route.path in ALLOWED_MOUNTS, f"montagem nao prevista: {route.path}"
        elif isinstance(route, APIRoute | Route):
            methods = route.methods or set()
            found |= {(m, prefix + route.path) for m in methods if m not in ("HEAD", "OPTIONS")}
    return found


def served_endpoints(app: FastAPI) -> list[Endpoint]:
    """Todas as rotas da app; falha se o esquema OpenAPI e a tabela real de rotas divergirem.

    O OpenAPI nao ve rotas com `include_in_schema=False`; a tabela real nao e afetada por isso.
    Exigir que os dois conjuntos coincidam impede que uma rota escondida escape ao teste de
    autenticacao e CSRF.
    """
    declared, served = openapi_endpoints(app), route_table_endpoints(app.routes)
    hidden, missing = sorted(served - declared), sorted(declared - served)
    assert not hidden and not missing, (
        f"rotas servidas fora do esquema OpenAPI: {hidden}; no esquema mas nao servidas: {missing}"
    )
    return sorted(served)


def app_endpoints(rig: PanelRig) -> list[Endpoint]:
    return served_endpoints(rig.app)


def concrete_path(template: str) -> str:
    def value(match: re.Match[str]) -> str:
        return "2026-W46" if match.group(0) == "{week}" else str(uuid.uuid4())

    return re.sub(r"\{[^}]+\}", value, template)


def test_guard_derivation_catches_hidden_routes_and_mounts() -> None:
    """A derivacao tem de falhar quando ha uma rota fora do esquema ou uma montagem estranha."""
    router = APIRouter()

    @router.get("/visivel")
    def visible() -> None: ...

    @router.post("/escondida", include_in_schema=False)
    def hidden() -> None: ...

    tiny = FastAPI()
    tiny.include_router(router)
    assert openapi_endpoints(tiny) == {("GET", "/visivel")}
    assert ("POST", "/escondida") in route_table_endpoints(tiny.routes)
    with pytest.raises(AssertionError, match="escondida"):
        served_endpoints(tiny)

    mounted = FastAPI()
    mounted.mount("/outra", FastAPI())
    with pytest.raises(AssertionError, match="/outra"):
        served_endpoints(mounted)


def test_real_app_exposes_no_hidden_route(make_panel: MakePanel) -> None:
    rig = make_panel([])
    endpoints = app_endpoints(rig)
    assert len(endpoints) == len(set(endpoints))
    paths = {path for _, path in endpoints}
    # as rotas de cada modulo (routes_*) estao todas na tabela servida
    for module in (routes_batch, routes_feedback, routes_kb, routes_media, routes_plan):
        assert route_table_endpoints(module.router.routes) <= set(endpoints), module.__name__
    assert {
        "/lote/{week}",
        "/lote/{week}/estado",
        "/post/{post_id}/aprovar",
        "/post/{post_id}/editar",
        "/post/{post_id}/alteracoes",
        "/post/{post_id}/rejeitar",
        "/post/{post_id}/gerar",
        "/feedback",
        "/feedback/{example_id}/desativar",
        "/biblioteca",
        "/biblioteca/{asset_id}/desativar",
        "/media/{asset_id}",
    } <= paths


def test_every_new_route_requires_auth_and_csrf(make_panel: MakePanel) -> None:
    """Derivado da app: uma rota futura (ex.: o lote) tambem tem de passar."""
    rig = make_panel([])
    endpoints = app_endpoints(rig)
    paths = {path for _, path in endpoints}
    assert {"/base", "/base/{item_id}/desativar", "/plano", "/plano/{artifact_id}"} <= paths
    assert any("/promover" in path for path in paths)
    assert {method for method, _ in endpoints} <= {"GET", "POST"}  # nada de PUT/DELETE esquecidos
    for method, template in endpoints:
        path, label = concrete_path(template), (method, template)
        anonymous = rig.client.request(method, path, follow_redirects=False)
        assert anonymous.status_code == 401, label
        if method == "GET":
            assert rig.get(path, {"Host": "evil.example"}).status_code == 400, label
            continue
        assert rig.post(path, with_token=False).status_code == 403, label
        wrong = rig.post(path, {"csrf_token": "x" * 64}, with_token=False)
        assert wrong.status_code == 403, label
        cross_site = rig.post(path, headers={"Sec-Fetch-Site": "cross-site"})
        assert cross_site.status_code == 403, label
        foreign_origin = rig.post(path, headers={"Origin": "http://evil.example"})
        assert foreign_origin.status_code == 403, label
        assert rig.post(path, headers={"Host": "evil.example"}).status_code == 400, label


def test_actor_comes_from_the_session_not_from_the_form(make_panel: MakePanel) -> None:
    rig = make_panel([])
    response = rig.post("/base", {**PRICE_FORM, "actor": "intruso", "created_by": "intruso"})
    assert response.status_code == 303
    [item] = kb_items(rig)
    assert item.created_by == LOGIN
    with rig.factory() as session:
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "kb.add")).one()
    assert audit.actor == LOGIN


# ---------------------------------------------------------------- base de conhecimento


def test_add_price_item_form(make_panel: MakePanel) -> None:
    rig = make_panel([])
    page = rig.get("/base")
    assert page.status_code == 200
    assert 'action="/base"' in page.text and 'name="price_amount"' in page.text
    response = rig.post("/base", PRICE_FORM)
    assert response.status_code == 303 and response.headers["location"] == "/base"
    [item] = kb_items(rig)
    assert (item.kind, item.key, item.price_amount, item.price_currency) == (
        "preco",
        "bolo-aniversario",
        Decimal("12.50"),
        "EUR",
    )
    assert (item.valid_from, item.valid_until) == (date(2026, 11, 1), date(2026, 12, 1))
    assert item.source_kind == "verificado"
    listing = rig.get("/base").text
    assert "Bolo de aniversário" in listing and "12.50 EUR" in listing
    assert "2026-11-01" in listing and "2026-12-01" in listing
    assert f"/base/{item.id}/desativar" in listing


def test_price_accepts_comma_decimal_and_lowercase_currency(make_panel: MakePanel) -> None:
    rig = make_panel([])
    form = {**PRICE_FORM, "price_amount": "12,50", "price_currency": "eur"}
    assert rig.post("/base", form).status_code == 303
    assert (kb_items(rig)[0].price_amount, kb_items(rig)[0].price_currency) == (
        Decimal("12.50"),
        "EUR",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"valid_until": ""},  # preco sem validade
        {"price_currency": ""},  # preco sem moeda
        {"price_amount": ""},  # preco sem montante
        {"valid_until": "2026-11-01"},  # janela vazia (preco exige fim > inicio)
        {"valid_until": "2026-10-01"},  # janela invertida
        {"price_amount": "abc"},
        {"price_amount": "-1"},
        {"valid_from": "amanha"},
        {"valid_from": ""},
        {"valid_until": "2026-13-45"},
        {"kind": "inexistente"},
        {"source_kind": "inventado"},
        {"price_currency": "EURO"},
        {"key": ""},
        {"title": "x" * 121},
        {"body": "y" * 2001},
    ],
)
def test_invalid_kb_form_is_422_and_saves_nothing(
    make_panel: MakePanel, overrides: dict[str, str]
) -> None:
    rig = make_panel([])
    response = rig.post("/base", {**PRICE_FORM, **overrides})
    assert response.status_code == 422, response.text
    assert kb_items(rig) == []


def test_kb_error_text_never_echoes_input(make_panel: MakePanel) -> None:
    rig = make_panel([])
    payload = "<script>alert(1)</script>"
    response = rig.post("/base", {**PRICE_FORM, "kind": payload})
    assert response.status_code == 422
    assert payload not in response.text and "alert(1)" not in response.text
    assert "ValidationError" not in response.text and "pydantic" not in response.text


def test_duplicate_kb_item_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert rig.post("/base", PRICE_FORM).status_code == 303
    again = rig.post("/base", PRICE_FORM)
    assert again.status_code == 422
    assert "bolo-aniversario" not in again.text
    assert len(kb_items(rig)) == 1


def test_non_price_item_without_validity_end_is_accepted(make_panel: MakePanel) -> None:
    rig = make_panel([])
    form = {"kind": "horario", "key": "loja", "title": "Horário", "body": "9h-18h."}
    response = rig.post("/base", {**form, "valid_from": "2026-11-01"})
    assert response.status_code == 303, response.text
    [item] = kb_items(rig)
    assert item.valid_until is None and item.price_amount is None
    assert item.source_kind == "dito_pelo_responsavel"  # origem por omissao: dito pelo responsavel


def test_deactivate_item_removes_it_from_the_listing(make_panel: MakePanel) -> None:
    rig = make_panel([])
    item_id = add_item(rig)
    assert "Oferta de outono" in rig.get("/base").text
    response = rig.post(f"/base/{item_id}/desativar")
    assert response.status_code == 303 and response.headers["location"] == "/base"
    assert "Oferta de outono" not in rig.get("/base").text
    assert not kb_items(rig)[0].is_active  # nunca se apaga
    assert rig.post(f"/base/{item_id}/desativar").status_code == 303  # idempotente
    with rig.factory() as session:
        actions = list(session.scalars(select(AuditLog.action).order_by(AuditLog.at)))
    assert actions.count("kb.deactivate") == 1


def test_deactivate_unknown_or_foreign_item_is_422(make_panel: MakePanel) -> None:
    rig = make_panel([])
    assert rig.post(f"/base/{uuid.uuid4()}/desativar").status_code == 422
    assert rig.post("/base/nao-e-um-id/desativar").status_code == 422
    other_brand = rig.new_brand()
    entry = NewKbEntry(
        kind="outro",
        key="x",
        title="Alheio",
        body="b",
        valid_from=date(2026, 11, 1),
        source_kind="suposto",
    )
    foreign_id = rig.content.kb.add(other_brand, entry, "ana").id
    assert rig.post(f"/base/{foreign_id}/desativar").status_code == 422
    assert kb_items(rig)[0].is_active
    assert "Alheio" not in rig.get("/base").text


def test_kb_page_groups_items_by_kind(make_panel: MakePanel) -> None:
    rig = make_panel([])
    add_item(rig)
    rig.post("/base", PRICE_FORM)
    html = rig.get("/base").text
    assert html.index("Oferta de outono") != html.index("Bolo de aniversário")
    assert 'id="kind-oferta"' in html and 'id="kind-preco"' in html


# ---------------------------------------------------------------- promover topico


def test_promote_topic_button(make_panel: MakePanel) -> None:
    rig = make_panel(planning_script())
    profile_id = rig.confirm_profile()
    page = rig.get(f"/perfil/{profile_id}").text
    assert f'action="/perfil/{profile_id}/promover"' in page
    assert 'name="topic" value="o_que_vende"' in page
    response = rig.post(f"/perfil/{profile_id}/promover", {"topic": "o_que_vende"})
    assert response.status_code == 303 and response.headers["location"] == "/base"
    [item] = kb_items(rig)
    assert (item.kind, item.key, item.source_kind) == (
        "outro",
        "o_que_vende",
        "dito_pelo_responsavel",
    )
    assert item.source_ref == f"perfil:{profile_id}:o_que_vende"
    assert item.created_by == LOGIN
    assert "Pao artesanal" in rig.get("/base").text
    assert rig.post(f"/perfil/{profile_id}/promover", {"topic": "o_que_vende"}).status_code == 303
    assert len(kb_items(rig)) == 1  # idempotente no mesmo dia


@pytest.mark.parametrize("fields", [{"topic": "inventado"}, {"topic": ""}, {}])
def test_promote_unknown_topic_is_422(make_panel: MakePanel, fields: dict[str, str]) -> None:
    rig = make_panel(planning_script())
    profile_id = rig.confirm_profile()
    assert rig.post(f"/perfil/{profile_id}/promover", fields).status_code == 422
    assert kb_items(rig) == []


def test_promote_requires_the_active_profile(make_panel: MakePanel) -> None:
    rig = make_panel(planning_script())
    session_id = rig.start_session()
    for number in range(3):
        rig.answer(session_id, f"Resposta {number}.", f"k{number}")
    submitted_id = rig.post_id(f"/entrevista/{session_id}/perfil")
    page = rig.get(f"/perfil/{submitted_id}").text
    assert "/promover" not in page  # so o perfil ativo tem o botao
    response = rig.post(f"/perfil/{submitted_id}/promover", {"topic": "o_que_vende"})
    assert response.status_code == 409
    assert "não é o perfil ativo" in response.text
    assert "Falta um passo anterior" not in response.text
    assert kb_items(rig) == []


def test_promote_refuses_other_artifact_types(make_panel: MakePanel) -> None:
    rig = make_panel(planning_script())
    rig.confirm_profile()
    diagnosis_id = rig.post_id("/diagnostico")
    response = rig.post(f"/perfil/{diagnosis_id}/promover", {"topic": "o_que_vende"})
    assert response.status_code == 404
    assert kb_items(rig) == []


# ---------------------------------------------------------------- propor, ver, decidir o plano


def test_propose_and_approve_plan_creates_posts(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel, slots=3)
    plan_id = rig.propose_plan()
    record = rig.store.get(plan_id)
    assert record.type is ArtifactType.PLANO and record.state is ArtifactState.SUBMITTED
    assert record.payload["semana"] == WEEK  # por omissao, a semana seguinte
    page = rig.get(f"/plano/{plan_id}").text
    assert f"/plano/{plan_id}/aprovar" in page and f"/plano/{plan_id}/rejeitar" in page
    assert count_rows(rig, Post) == 0  # ler a pagina nao aprova
    response = rig.post(f"/plano/{plan_id}/aprovar")
    assert response.status_code == 303 and response.headers["location"] == f"/lote/{WEEK}"
    assert rig.store.get(plan_id).state is ArtifactState.APPROVED
    assert count_rows(rig, Post) == 3 and count_rows(rig, Job) == 3
    with rig.factory() as session:
        audit = session.scalars(select(AuditLog).where(AuditLog.action == "plan.approve")).one()
    assert audit.actor == LOGIN
    after = rig.get(f"/plano/{plan_id}").text
    assert f"/plano/{plan_id}/aprovar" not in after  # ja decidido: so leitura
    assert "3 publicações" in after


def test_approving_twice_does_not_duplicate_posts(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    plan_id = rig.propose_plan()
    assert rig.post(f"/plano/{plan_id}/aprovar").status_code == 303
    assert rig.post(f"/plano/{plan_id}/aprovar").status_code == 409
    assert count_rows(rig, Post) == 3 and count_rows(rig, Job) == 3


# ------------------------------------------------------- reaprovar com publicacoes decididas

CONFIRM_MESSAGE = "cancelaria publicações aprovadas ou em revisão"


def submit_variant(
    rig: PanelRig, plan_id: str, *, keep: int | None = None, themes: dict[int, str] | None = None
) -> str:
    """Novo plano `submitted` da mesma semana: ate `keep` slots, com temas trocados (0-based)."""
    payload = dict(rig.store.get(plan_id).payload)
    slots = [dict(slot) for slot in payload["slots"]][:keep]  # type: ignore[attr-defined]
    for index, theme in (themes or {}).items():
        slots[index]["tema"] = theme
    created = rig.store.create(rig.brand_id, ArtifactType.PLANO, {**payload, "slots": slots})
    rig.store.submit(created.id, "ana")
    return created.id


def set_lifecycle(rig: PanelRig, slot: int, lifecycle: str) -> None:
    with rig.factory.begin() as session:
        session.execute(
            update(Post).where(Post.slot_key == f"{WEEK}-{slot}").values(lifecycle=lifecycle)
        )


def lifecycles(rig: PanelRig) -> dict[str, str]:
    with rig.factory() as session:
        return {p.slot_key: p.lifecycle for p in session.scalars(select(Post))}


def decided_week(make_panel: MakePanel) -> tuple[PanelRig, str]:
    """Plano de 3 slots aprovado; o slot 2 esta por rever e o 3 aprovado. Devolve o plano antigo."""
    rig = planning_rig(make_panel, slots=3)
    first = rig.propose_plan()
    assert rig.post(f"/plano/{first}/aprovar").status_code == 303
    set_lifecycle(rig, 2, "in_review")
    set_lifecycle(rig, 3, "approved")
    return rig, first


def test_plan_page_of_a_fresh_week_has_no_preview_nor_confirmation(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    html = rig.get(f"/plano/{rig.propose_plan()}").text
    assert "confirm_cancel" not in html and "Já existem publicações" not in html


def test_preview_and_confirmation_checkbox_when_decided_posts_would_be_cancelled(
    make_panel: MakePanel,
) -> None:
    rig, first = decided_week(make_panel)
    second = submit_variant(rig, first, keep=2, themes={1: "Tema novo do slot dois"})

    html = rig.get(f"/plano/{second}").text

    assert "Já existem publicações nesta semana" in html
    assert 'name="confirm_cancel"' in html and 'type="checkbox"' in html
    assert "required" in html[html.index('name="confirm_cancel"') :][:200]
    assert html.index('name="confirm_cancel"') < html.index(f"/plano/{second}/rejeitar")
    assert f'action="/plano/{second}/aprovar"' in html
    assert "Tema novo do slot dois" in html and "→" in html
    assert "Atenção" in html and "ficaria cancelada" in html  # aviso claro
    for label in ("Por rever", "Aprovada", "Cancelada", "Mantida"):
        assert label in html
    assert count_rows(rig, Job) == 3 and lifecycles(rig)[f"{WEEK}-3"] == "approved"  # so leitura


def test_preview_without_decided_cancellations_has_no_checkbox(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    first = rig.propose_plan()
    assert rig.post(f"/plano/{first}/aprovar").status_code == 303
    second = submit_variant(rig, first, keep=2, themes={0: "Outro tema"})
    html = rig.get(f"/plano/{second}").text
    assert "Já existem publicações nesta semana" in html and "Outro tema" in html
    assert "confirm_cancel" not in html and "Atenção" not in html
    assert rig.post(f"/plano/{second}/aprovar").status_code == 303
    assert lifecycles(rig)[f"{WEEK}-3"] == "cancelled"


def test_approving_without_confirmation_is_409_and_writes_nothing(make_panel: MakePanel) -> None:
    rig, first = decided_week(make_panel)
    second = submit_variant(rig, first, keep=1)
    jobs, states = count_rows(rig, Job), lifecycles(rig)

    for form in ({}, {"confirm_cancel": ""}, {"confirm_cancel": "talvez"}):
        response = rig.post(f"/plano/{second}/aprovar", form)
        assert response.status_code == 409, form
        assert CONFIRM_MESSAGE in response.text and first not in response.text

    assert rig.store.get(second).state is ArtifactState.SUBMITTED
    assert (count_rows(rig, Job), lifecycles(rig)) == (jobs, states)
    with rig.factory() as session:
        approvals = session.scalars(select(AuditLog).where(AuditLog.action == "plan.approve"))
        assert len(list(approvals)) == 1  # so a primeira aprovacao


def test_approving_with_confirmation_cancels_the_decided_posts(make_panel: MakePanel) -> None:
    rig, first = decided_week(make_panel)
    second = submit_variant(rig, first, keep=1)

    response = rig.post(f"/plano/{second}/aprovar", {"confirm_cancel": "1"})

    assert response.status_code == 303 and response.headers["location"] == f"/lote/{WEEK}"
    assert rig.store.get(second).state is ArtifactState.APPROVED
    assert lifecycles(rig) == {
        f"{WEEK}-1": "planned",
        f"{WEEK}-2": "cancelled",
        f"{WEEK}-3": "cancelled",
    }


def test_confirmation_is_recomputed_when_the_page_was_stale(make_panel: MakePanel) -> None:
    """A pagina foi aberta quando o slot 3 ainda estava por gerar; o responsavel aprovou-o."""
    rig = planning_rig(make_panel)
    first = rig.propose_plan()
    assert rig.post(f"/plano/{first}/aprovar").status_code == 303
    second = submit_variant(rig, first, keep=2)
    assert "confirm_cancel" not in rig.get(f"/plano/{second}").text
    set_lifecycle(rig, 3, "approved")
    assert rig.post(f"/plano/{second}/aprovar").status_code == 409
    assert lifecycles(rig)[f"{WEEK}-3"] == "approved"


def test_preview_themes_are_escaped_and_decided_posts_show_the_old_theme(
    make_panel: MakePanel,
) -> None:
    rig, first = decided_week(make_panel)
    hostile = "<script>alert(1)</script>"
    with rig.factory.begin() as session:
        post = session.scalars(select(Post).where(Post.slot_key == f"{WEEK}-2")).one()
        post.brief = {**post.brief, "tema": "<img src=x onerror=alert(2)>"}
    second = submit_variant(rig, first, themes={1: hostile})

    html = rig.get(f"/plano/{second}").text

    assert hostile not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
    assert "Mantida" in html and "outro conteúdo" in html  # decidida: mantem o texto antigo
    assert html.count("<script") == html.count('<script src="/static/')


def test_decided_plan_page_has_no_preview(make_panel: MakePanel) -> None:
    rig, first = decided_week(make_panel)
    html = rig.get(f"/plano/{first}").text
    assert "Já existem publicações" not in html and "confirm_cancel" not in html


def test_reject_plan_creates_nothing_and_is_read_only(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    plan_id = rig.propose_plan()
    response = rig.post(f"/plano/{plan_id}/rejeitar", {"motivo": "Demasiadas publicações."})
    assert response.status_code == 303 and response.headers["location"] == f"/plano/{plan_id}"
    assert rig.store.get(plan_id).state is ArtifactState.REJECTED
    assert count_rows(rig, Post) == 0 and count_rows(rig, Job) == 0
    page = rig.get(f"/plano/{plan_id}").text
    assert f"/plano/{plan_id}/aprovar" not in page and f"/plano/{plan_id}/rejeitar" not in page
    assert rig.post(f"/plano/{plan_id}/rejeitar").status_code == 409
    assert rig.post(f"/plano/{plan_id}/aprovar").status_code == 409


def test_plan_page_shows_slots_assumptions_and_resolved_kb_titles(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    item_id = add_item(rig, title="Oferta de outono")
    plan_id = rig.propose_plan()
    record = rig.store.get(plan_id)
    slots = [dict(slot) for slot in record.payload["slots"]]  # type: ignore[attr-defined]
    slots[1]["kb_refs"] = [item_id, str(uuid.uuid4())]
    edited = rig.store.create(rig.brand_id, ArtifactType.PLANO, {**record.payload, "slots": slots})
    rig.store.submit(edited.id, "ana")
    html = rig.get(f"/plano/{edited.id}").text
    assert WEEK in html
    for slot in slots:
        assert str(slot["tema"]) in html and str(slot["pilar"]) in html
        assert str(slot["data"]) in html
    assert "Oferta de outono" in html  # kb_ref resolvido para o titulo
    assert "item da base desconhecido" in html  # ref sem item correspondente
    assert "Experiência 1" in html  # slot 0 liga-se a experiencia 0 da estrategia
    assert "A capacidade de entrega suporta" in html  # suposicoes


def test_plan_page_escapes_model_text(make_panel: MakePanel) -> None:
    rig = make_panel([])
    script_tag = "<script>alert(1)</script>"
    breaker = '"><img src=x onerror=alert(2)>'
    item_id = add_item(rig, title="<b onmouseover=alert(3)>título</b>")
    slot = {
        "slot_key": f"{WEEK}-1",
        "data": "2026-11-09",
        "canais": ["instagram"],
        "tema": script_tag,
        "pilar": breaker,
        "objetivo": "<svg onload=alert(4)>",
        "experiment_index": None,
        "kb_refs": [item_id],
        "justificacao": "<iframe src=//evil.example></iframe>",
    }
    payload = {"semana": WEEK, "slots": [slot], "suposicoes": ["<img src=x onerror=alert(5)>"]}
    plan = rig.store.create(rig.brand_id, ArtifactType.PLANO, payload)
    rig.store.submit(plan.id, "ana")
    html = rig.get(f"/plano/{plan.id}").text
    for raw in (script_tag, breaker, "<svg onload", "<iframe", "<img src=x", "<b onmouseover"):
        assert raw not in html, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b onmouseover=alert(3)&gt;" in html
    assert "&#34;&gt;&lt;img src=x onerror=alert(2)&gt;" in html
    kb_html = rig.get("/base").text
    assert "<b onmouseover" not in kb_html and "&lt;b onmouseover=alert(3)&gt;" in kb_html
    home = rig.get("/").text
    assert script_tag not in home


def test_kb_body_is_escaped_on_the_listing(make_panel: MakePanel) -> None:
    rig = make_panel([])
    form = {**PRICE_FORM, "body": "<script>alert(9)</script>", "key": "<i>chave</i>"}
    assert rig.post("/base", form).status_code == 303
    html = rig.get("/base").text
    assert "<script>alert(9)" not in html and "<i>chave</i>" not in html
    assert "&lt;script&gt;alert(9)&lt;/script&gt;" in html


def test_pages_have_no_inline_script_or_style(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    plan_id = rig.propose_plan()
    add_item(rig)
    for path in ("/base", f"/plano/{plan_id}", "/"):
        html = rig.get(path).text
        assert html.count("<script") == html.count('<script src="/static/')
        assert "<style" not in html and ' style="' not in html and " onclick=" not in html


def test_plan_page_of_other_type_or_brand_is_404(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    strategy = rig.store.active(rig.brand_id, ArtifactType.ESTRATEGIA)
    assert strategy is not None
    assert rig.get(f"/plano/{strategy.id}").status_code == 404
    assert rig.post(f"/plano/{strategy.id}/aprovar").status_code == 404
    assert rig.post(f"/plano/{strategy.id}/rejeitar").status_code == 404
    other = rig.new_brand()
    foreign = rig.store.create(
        other, ArtifactType.PLANO, {"semana": WEEK, "slots": [], "suposicoes": []}
    )
    assert rig.get(f"/plano/{foreign.id}").status_code == 404
    assert rig.get(f"/plano/{uuid.uuid4()}").status_code == 404
    assert rig.get("/plano/nao-e-um-id").status_code == 422


def test_propose_with_explicit_week(make_panel: MakePanel) -> None:
    rig = make_panel(planning_script(week="2026-W47"))
    rig.approve_strategy()
    plan_id = rig.propose_plan({"semana": "2026-W47"})
    assert rig.store.get(plan_id).payload["semana"] == "2026-W47"


@pytest.mark.parametrize("week", ["2026-W99", "amanha", "2026-w46", "2026-W46\n", "<script>"])
def test_propose_with_invalid_week_is_422(make_panel: MakePanel, week: str) -> None:
    rig = planning_rig(make_panel)
    response = rig.post("/plano", {"semana": week})
    assert response.status_code == 422
    assert "<script>" not in response.text
    used = len(rig.llm.requests)
    assert rig.post("/plano", {"semana": week}).status_code == 422
    assert len(rig.llm.requests) == used  # recusado antes de chamar o modelo


def test_propose_without_strategy_is_409(make_panel: MakePanel) -> None:
    rig = make_panel(planning_script())
    rig.confirm_profile()
    response = rig.post("/plano")
    assert response.status_code == 409
    assert count_rows(rig, Post) == 0


def test_propose_twice_is_409_pending(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    rig.propose_plan()
    assert rig.post("/plano").status_code == 409


def test_propose_in_progress_is_409(make_panel: MakePanel, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = planning_rig(make_panel)

    def busy(brand_id: str, week: str | None = None) -> object:
        raise ProposalInProgress("marcador activo")

    monkeypatch.setattr(rig.content.planning, "propose_plan", busy)
    response = rig.post("/plano")
    assert response.status_code == 409
    assert "marcador" not in response.text


def test_failed_run_is_502_generic_without_model_text(make_panel: MakePanel) -> None:
    script = [*planning_script()[:-1]]  # ate a estrategia; o plano falha
    script += [json_response({"semana": MODEL_SECRET}) for _ in range(4)]
    rig = make_panel(script)
    rig.approve_strategy()
    response = rig.post("/plano")
    assert response.status_code == 502
    assert UPSTREAM_MESSAGE in response.text
    assert MODEL_SECRET not in response.text and "Traceback" not in response.text
    assert count_rows(rig, Post) == 0


# ---------------------------------------------------------------- pagina inicial


def test_home_links_plan_and_pending(make_panel: MakePanel) -> None:
    rig = planning_rig(make_panel)
    before = rig.get("/").text
    assert 'action="/plano"' in before and "Propor plano" in before
    assert 'href="/base"' in before
    plan_id = rig.propose_plan()
    submitted = rig.get("/").text
    assert f'href="/plano/{plan_id}"' in submitted  # pendente e ligacao ao plano
    assert rig.post(f"/plano/{plan_id}/aprovar").status_code == 303
    approved = rig.get("/").text
    assert f'href="/plano/{plan_id}"' in approved and WEEK in approved
    assert "Nenhuma publicação por rever" in approved
    with rig.factory.begin() as session:
        first = session.scalars(select(Post).order_by(Post.slot_key)).first()
        assert first is not None
        session.execute(update(Post).where(Post.id == first.id).values(lifecycle="in_review"))
    assert "1 publicação por rever" in rig.get("/").text


def test_home_plan_button_is_disabled_without_strategy(make_panel: MakePanel) -> None:
    rig = make_panel([])
    html = rig.get("/").text
    assert "Propor plano" in html
    assert "Precisa primeiro de uma estratégia aprovada" in html
    assert 'href="/plano/' not in html
