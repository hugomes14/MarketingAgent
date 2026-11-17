"""Nucleo do pacote: percurso especializado sem posts e teto de custo por pacote (Plano 2C, T2).

Cobre `PackageCostGuard` (gasto da familia de triggers, reservas pendentes, `cap`), a quota de
imagens por familia, `SpecializedPath.produce` (so contabilidade do motor) e o teto no percurso
habitual (`ContentPipeline.generate`).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import PROFILE_PAYLOAD, ContentRig, Factory, Script, build_rig, package_pass
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketing_agent.cli import _build_pipeline
from marketing_agent.clock import FixedClock
from marketing_agent.config import DEFAULT_PACKAGE_MAX_COST_USD, Settings
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package import (
    PackageContext,
    PackageCostExceeded,
    PackageCostGuard,
    PackageRequest,
    SpecializedPath,
)
from marketing_agent.content.package_costs import (
    COST_CEILING_REASON,
    PackageTriggers,
    family_billed_images,
    family_remaining_images,
    remaining_images,
)
from marketing_agent.content.package_media import DesignMedia
from marketing_agent.content.rounds import COST_CEILING_NOTE, PackageRounds
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import (
    AgentRun,
    AgentStep,
    Approval,
    ArtifactHead,
    Brand,
    CostEntry,
    CostReservation,
    FeedbackExample,
    Post,
    PostRevision,
)
from marketing_agent.harness.engine import RunResult
from marketing_agent.harness.spec import AgentSpec
from marketing_agent.llm import fake_scenarios

MakeRig = Callable[..., ContentRig]
CEILING = Decimal("1.50")
BOUNDARY_TABLES = (Post, PostRevision, Approval, FeedbackExample, ArtifactHead)


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> MakeRig:
    def make(script: Script | None = None, **options: object) -> ContentRig:
        return build_rig(
            session_factory,
            clock,
            brand_id,
            script or [],
            media_dir=tmp_path / "media",
            **options,  # type: ignore[arg-type]
        )

    return make


def seed_spend(
    factory: Factory,
    brand_id: str,
    trigger: str,
    *,
    spent: str = "0",
    open_: str = "0",
    unknown: str = "0",
    released: str = "0",
    image_steps: int = 0,
    now: datetime,
) -> str:
    """Uma execucao com o `trigger` e o seu gasto: lancamento acertado e reservas por estado.

    `image_steps` acrescenta lancamentos acertados de imagem (`units = {"images": 1}`).
    """
    with factory.begin() as session:
        run = AgentRun(
            brand_id=brand_id, role="copywriter", spec_version=1, trigger=trigger,
            status="succeeded", cost_usd=Decimal(spent), started_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        if Decimal(spent) > 0:
            _settled(session, run, Decimal(spent), {}, now)
        for _ in range(image_steps):
            _settled(session, run, Decimal("0.04"), {"images": 1}, now)
        for status, amount in (
            ("open", open_),
            ("unknown_outcome", unknown),
            ("released", released),
        ):
            if Decimal(amount) > 0:
                _reservation(session, run, status, Decimal(amount), now)
        return run.id


def _reservation(
    session: Session, run: AgentRun, status: str, amount: Decimal, now: datetime
) -> CostReservation:
    reservation = CostReservation(
        brand_id=run.brand_id, run_id=run.id, role=run.role, provider="fake", model_id="fake-1",
        price_version="v1", reserved_usd=amount, status=status, created_at=now,
    )  # fmt: skip
    session.add(reservation)
    session.flush()
    return reservation


def _settled(
    session: Session, run: AgentRun, cost: Decimal, units: dict[str, object], now: datetime
) -> None:
    reservation = _reservation(session, run, "settled", cost, now)
    session.add(
        CostEntry(
            brand_id=run.brand_id,
            reservation_id=reservation.id,
            role=run.role,
            run_id=run.id,
            provider="fake",
            model_id="fake-1",
            price_version="v1",
            tokens_in=0,
            tokens_out=0,
            units=units,
            cost_usd=cost,
            kind="measured",
            at=now,
        )  # fmt: skip
    )


def guard_for(rig: ContentRig, *triggers: str, ceiling: str = "1.50") -> PackageCostGuard:
    return PackageCostGuard(rig.factory, rig.brand_id, triggers, Decimal(ceiling))


def specialized(rig: ContentRig, ceiling: Decimal = CEILING) -> SpecializedPath:
    rounds = PackageRounds(
        rig.engine, load_content_specs(), DesignMedia(rig.library, rig.kits), "medium"
    )
    return SpecializedPath(rig.factory, rounds, ceiling)


def copy_payload() -> CopyPayload:
    return CopyPayload(
        brief={"tema": "Pao do dia", "pilar": "Producao artesanal", "objetivo": "Mensagens",
               "kb_refs": []},
        channels=["instagram"],
        perfil_marca=PROFILE_PAYLOAD,
        pilares=["Producao artesanal"],
        accepted_proposals=[],
        kb_entries={},
        style_examples=[],
        negative_examples=[],
        reviewer_notes=None,
        owner_comment=None,
    )  # fmt: skip


def request_for(rig: ContentRig, triggers: PackageTriggers) -> PackageRequest:
    return PackageRequest(rig.brand_id, triggers, PackageContext(copy_payload(), None))


def count_rows(factory: Factory, model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


@dataclass
class SpecSpy:
    """Regista o papel e o limite de custo do spec de cada `engine.run`."""

    seen: list[tuple[str, Decimal]]

    def install(self, rig: ContentRig, monkeypatch: pytest.MonkeyPatch) -> None:
        real_run = rig.engine.run

        def spy(spec: AgentSpec, *args, **kwargs) -> RunResult:  # type: ignore[no-untyped-def]
            self.seen.append((spec.role, spec.limits.max_cost_usd))
            return real_run(spec, *args, **kwargs)

        monkeypatch.setattr(rig.engine, "run", spy)


# --- familia de triggers ---------------------------------------------------------------------


def test_trigger_family_conventions() -> None:
    job = PackageTriggers.for_job("J1")
    assert job.family == ("post.copy:J1", "post.design:J1", "post.review:J1")
    assert (job.copy, job.design, job.review) == job.family
    single = PackageTriggers.single("eval_item:E1")
    assert single.family == ("eval_item:E1",)
    assert {single.of_step("copy"), single.of_step("design"), single.of_step("review")} == {
        "eval_item:E1"
    }


# --- guard: gasto -----------------------------------------------------------------------------


def test_guard_counts_pending_reservations(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig()
    seed_spend(
        rig.factory, rig.brand_id, "eval_item:E1", spent="0.200000", open_="0.400000",
        unknown="0.300000", released="0.500000", now=clock.now(),
    )  # fmt: skip
    guard = guard_for(rig, "eval_item:E1", ceiling="1.00")
    # Acertado (0.2) + aberta (0.4) + desfecho desconhecido (0.3); a libertada nao conta e a
    # reserva acertada nao se conta duas vezes (ja esta no lancamento).
    assert guard.spent_usd() == Decimal("0.9")
    capped = guard.cap(load_content_specs().copywriter)
    assert capped.limits.max_cost_usd == Decimal("0.1")


def test_guard_spent_is_zero_without_runs(make_rig: MakeRig) -> None:
    rig = make_rig()
    assert guard_for(rig, "eval_item:nada").spent_usd() == Decimal(0)


def test_guard_sums_every_trigger_of_the_family_only(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig()
    now = clock.now()
    family = PackageTriggers.for_job("J1").family
    for trigger, amount in zip(family, ("0.1", "0.2", "0.3"), strict=True):
        seed_spend(rig.factory, rig.brand_id, trigger, spent=amount, now=now)
    seed_spend(rig.factory, rig.brand_id, "post.copy:J2", spent="0.7", now=now)
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="0.9", now=now)
    assert guard_for(rig, *family).spent_usd() == Decimal("0.6")
    assert guard_for(rig, "eval_item:E1").spent_usd() == Decimal("0.9")
    assert guard_for(rig, "post.copy:J2").spent_usd() == Decimal("0.7")


def test_guard_is_scoped_to_the_brand(
    make_rig: MakeRig, clock: FixedClock, session_factory: Factory
) -> None:
    rig = make_rig()
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    seed_spend(rig.factory, other_id, "eval_item:E1", spent="0.9", now=clock.now())
    assert guard_for(rig, "eval_item:E1").spent_usd() == Decimal(0)


# --- guard: cap -------------------------------------------------------------------------------


def test_cap_never_mutates_the_shared_spec(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig()
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.400000", now=clock.now())
    spec = load_content_specs().designer
    original = spec.limits.max_cost_usd
    capped = guard_for(rig, "eval_item:E1").cap(spec)
    assert capped is not spec and capped.limits is not spec.limits
    assert capped.limits.max_cost_usd == Decimal("0.1")
    assert spec.limits.max_cost_usd == original == Decimal("0.50")
    assert capped.model_copy(update={"limits": spec.limits}) == spec  # so o limite mudou


def test_cap_keeps_the_role_limit_when_the_ceiling_leaves_more(
    make_rig: MakeRig,
) -> None:
    rig = make_rig()
    spec = load_content_specs().copywriter
    capped = guard_for(rig, "eval_item:E1").cap(spec)
    assert capped.limits.max_cost_usd == spec.limits.max_cost_usd == Decimal("0.30")


def test_cap_raises_when_the_ceiling_is_exactly_reached(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig()
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.500000", now=clock.now())
    guard = guard_for(rig, "eval_item:E1")
    with pytest.raises(PackageCostExceeded) as caught:
        guard.cap(load_content_specs().reviewer)
    assert str(caught.value) == COST_CEILING_REASON == "pacote: teto de custo atingido"


def test_cap_raises_when_the_ceiling_is_exceeded(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig()
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.200000", open_="0.400000",
               now=clock.now())  # fmt: skip
    with pytest.raises(PackageCostExceeded):
        guard_for(rig, "eval_item:E1").cap(load_content_specs().reviewer)


def test_cap_allows_the_last_millionth_of_a_dollar(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig()
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.499999", now=clock.now())
    capped = guard_for(rig, "eval_item:E1").cap(load_content_specs().designer)
    assert capped.limits.max_cost_usd == Decimal("0.000001")


def test_cap_decimal_arithmetic_is_exact_and_floors_to_the_database_scale(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig()
    now = clock.now()
    # Tres parcelas que em float nao somam exatamente.
    for amount in ("0.100000", "0.200000", "0.300000"):
        seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent=amount, now=now)
    guard = guard_for(rig, "eval_item:E1", ceiling="0.6000019")
    assert guard.spent_usd() == Decimal("0.6")
    capped = guard.cap(load_content_specs().designer)
    assert capped.limits.max_cost_usd == Decimal("0.000001")  # 0.0000019 arredondado por defeito
    # Menos de um milionesimo de dolar por gastar ja conta como teto esgotado.
    with pytest.raises(PackageCostExceeded):
        guard_for(rig, "eval_item:E1", ceiling="0.6000009").cap(load_content_specs().designer)


def test_guard_rejects_a_non_positive_ceiling(make_rig: MakeRig) -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="teto"):
        guard_for(rig, "eval_item:E1", ceiling="0")


# --- quota de imagens por familia ------------------------------------------------------------


def test_image_quota_is_per_trigger_family_on_one_brand(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig()
    now = clock.now()
    seed_spend(rig.factory, rig.brand_id, "eval_item:A", image_steps=2, now=now)
    seed_spend(rig.factory, rig.brand_id, "eval_item:B", image_steps=1, now=now)
    seed_spend(rig.factory, rig.brand_id, "post.design:J1", image_steps=1, now=now)
    assert family_billed_images(rig.factory, rig.brand_id, ["eval_item:A"]) == 2
    assert family_remaining_images(rig.factory, rig.brand_id, ["eval_item:A"]) == 0
    assert family_remaining_images(rig.factory, rig.brand_id, ["eval_item:B"]) == 1
    assert family_remaining_images(rig.factory, rig.brand_id, ["eval_item:C"]) == 2
    # O job usa a mesma contagem pela sua familia; o caminho antigo continua a funcionar.
    job_family = PackageTriggers.for_job("J1").family
    assert family_remaining_images(rig.factory, rig.brand_id, job_family) == 1
    assert remaining_images(rig.factory, rig.brand_id, "J1") == 1
    assert remaining_images(rig.factory, rig.brand_id, "J2") == 2


def test_open_image_reservations_count_in_a_family_quota(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig()
    run_id = seed_spend(rig.factory, rig.brand_id, "eval_item:A", open_="0.04", now=clock.now())
    with rig.factory.begin() as session:
        step = AgentStep(run_id=run_id, seq=1, kind="tool", name="generate_image")
        session.add(step)
        session.flush()
        for reservation in session.scalars(select(CostReservation)):
            reservation.step_id = step.id
    assert family_remaining_images(rig.factory, rig.brand_id, ["eval_item:A"]) == 1
    assert family_remaining_images(rig.factory, rig.brand_id, ["eval_item:B"]) == 2


# --- SpecializedPath --------------------------------------------------------------------------


def test_specialized_path_writes_no_post_rows(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    before = {model: count_rows(rig.factory, model) for model in BOUNDARY_TABLES}
    runs_before = count_rows(rig.factory, AgentRun)

    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))

    assert result.outcome == "ok"
    assert {model: count_rows(rig.factory, model) for model in BOUNDARY_TABLES} == before
    assert count_rows(rig.factory, AgentRun) == runs_before + 3  # so contabilidade do motor
    assert rig.assets("final") == [] and rig.assets("componente_gerado") == []  # ativos: o chamador


def test_specialized_path_returns_the_package_in_memory(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass("aprovar", notes="Boa.", legenda="Legenda unica."))
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))

    assert result.outcome == "ok" and result.reason is None
    assert result.text is not None and result.text.draft.legenda == "Legenda unica."
    assert (result.text.verdict, result.review_rounds) == ("aprovar", 0)
    assert len(result.reviews) == 1 and result.image_checked is True
    assert result.visual is not None and result.final_image is not None
    assert result.final_image.data[:3] == b"\xff\xd8\xff"
    assert result.generated_background is not None  # fundo_ia_titulo
    assert result.is_ai_generated is True
    assert [component.tipo for component in result.components] == ["gerada", "logotipo", "texto"]
    assert result.as_package().design is result.design


def test_specialized_path_runs_are_linked_to_the_request_triggers(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    triggers = PackageTriggers.single("eval_item:E1")
    specialized(rig).produce(request_for(rig, triggers))
    with rig.factory() as session:
        found = set(session.scalars(select(AgentRun.trigger)).all())
    assert found == {"eval_item:E1"}
    assert guard_for(rig, "eval_item:E1").spent_usd() == Decimal(0)  # texto e imagem a custo 0


def test_specialized_path_same_rounds_as_before(make_rig: MakeRig) -> None:
    script = (
        package_pass("rever", ["texto_ilegivel"], "NOTA-UM", legenda="Um")
        + [
            *fake_scenarios.visual_draft(),
            *fake_scenarios.review("rever", ("limite_canal",), "NOTA-DOIS", imagem_verificada=True),
        ]
        + package_pass("rever", ["marca_incoerente"], "NOTA-TRES", legenda="Tres")
    )
    rig = make_rig(script)
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))

    names = {"RascunhoTexto": "copy", "RascunhoVisual": "design", "Revisao": "review"}
    kinds = [names[(request.json_schema or {})["title"]] for request in rig.llm.requests]
    assert kinds == ["copy", "design", "review", "design", "review", "copy", "design", "review"]
    # `rever` duas vezes: o pacote segue com as notas da ultima volta.
    assert result.outcome == "ok" and result.review_rounds == 2
    assert result.text is not None
    assert (result.text.verdict, result.text.violations) == ("rever", ["marca_incoerente"])
    assert result.text.notes == "NOTA-TRES"
    assert len(result.reviews) == 3


def test_specialized_path_image_failure_is_failed_with_the_text(make_rig: MakeRig) -> None:
    rig = make_rig(fake_scenarios.caption_draft(), with_kit=False)
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))
    assert result.outcome == "failed"
    assert result.text is not None and result.text.verdict is None
    assert result.design is None and result.reason == "Falta o kit de marca."
    assert result.review_rounds == 0 and result.components == ()
    with pytest.raises(ValueError, match="'ok'"):
        result.as_package()


def test_specialized_path_gives_each_family_its_own_image_quota(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig(package_pass())
    seed_spend(rig.factory, rig.brand_id, "eval_item:OUTRO", image_steps=2, now=clock.now())
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))
    assert (
        result.outcome == "ok" and rig.generated_requests() == 1
    )  # a quota do outro item nao conta


def test_specialized_path_respects_an_exhausted_family_quota(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig(package_pass())
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", image_steps=2, now=clock.now())
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))
    assert rig.generated_requests() == 0
    assert result.outcome == "failed" and result.reason == "limite de imagens geradas atingido"


# --- teto de custo do pacote ------------------------------------------------------------------


def test_guard_caps_package_total_across_attempts(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    triggers = PackageTriggers.single("eval_item:E1")
    # Uma tentativa anterior do mesmo pedido ja gastou 1.47 dos 1.50.
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.470000", now=clock.now())

    result = specialized(rig).produce(request_for(rig, triggers))

    assert result.outcome == "teto_pacote" and result.reason == COST_CEILING_REASON
    assert result.text is None and result.design is None
    assert rig.generated_requests() == 0 and rig.image_entries() == []  # 0.04 > 0.03: nada pago
    assert guard_for(rig, "eval_item:E1").spent_usd() <= CEILING


def test_cap_is_applied_to_every_engine_run_of_every_round(
    make_rig: MakeRig, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = package_pass("rever", ["texto_ilegivel", "limite_canal"], "Dois.") + package_pass()
    rig = make_rig(script, image_price="0.04")
    seed_spend(rig.factory, rig.brand_id, "eval_item:E1", spent="1.300000", now=clock.now())
    spy = SpecSpy([])
    spy.install(rig, monkeypatch)

    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))

    assert result.outcome == "ok"
    roles = [role for role, _ in spy.seen]
    assert roles == ["copywriter", "designer", "revisor"] * 2
    # Sobram 0.20 antes da 1.a imagem (Copywriter e Designer); o Revisor ja ve 0.16 (a imagem
    # custou 0.04) e a 2.a passagem reutiliza o fundo, por isso fica em 0.16. O motor ve sempre o
    # que sobra do teto, nunca o limite do papel (0.30 / 0.50).
    limits = [limit for _, limit in spy.seen]
    assert limits == [Decimal(value) for value in ("0.20", "0.20", "0.16", "0.16", "0.16", "0.16")]
    # O spec partilhado dos papeis nao foi tocado.
    specs = load_content_specs()
    assert (specs.copywriter.limits.max_cost_usd, specs.designer.limits.max_cost_usd) == (
        Decimal("0.30"),
        Decimal("0.50"),
    )


def test_package_ships_the_last_complete_pass_when_the_ceiling_runs_out(
    make_rig: MakeRig,
) -> None:
    script = package_pass("rever", ["imagem_inadequada"], "Mal.") + fake_scenarios.visual_draft()
    rig = make_rig(script, image_price="0.04")
    path = specialized(rig, ceiling=Decimal("0.05"))

    result = path.produce(request_for(rig, PackageTriggers.single("eval_item:E1")))

    # A 1.a passagem custa 0.04; o fundo novo da volta seguinte (0.04) nao cabe nos 0.01 que sobram.
    assert result.outcome == "ok" and result.text is not None
    assert result.text.notes == f"Mal.\nrevisao final indisponivel: {COST_CEILING_NOTE}"
    assert rig.generated_requests() == 1
    assert guard_for(rig, "eval_item:E1", ceiling="0.05").spent_usd() == Decimal("0.04")


def test_ceiling_stops_a_role_that_hits_the_capped_cost_limit_in_the_first_pass(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig(package_pass(), image_price="0.60")
    # Teto 0.30: o Designer fica com 0.30 (e nao 0.50); 0.60 por imagem e o teto do pacote.
    result = specialized(rig, ceiling=Decimal("0.30")).produce(
        request_for(rig, PackageTriggers.single("eval_item:E1"))
    )
    assert result.outcome == "teto_pacote"
    assert rig.generated_requests() == 0


def test_ceiling_does_not_reclassify_a_role_limit_below_the_ceiling(make_rig: MakeRig) -> None:
    # 0.60 por imagem excede o limite do Designer (0.50) mesmo com todo o teto disponivel:
    # continua a ser uma falha de imagem (comportamento anterior), nao o teto do pacote.
    rig = make_rig(package_pass(), image_price="0.60")
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))
    assert result.outcome == "failed"
    assert result.reason == "limite de custo do designer atingido"


def test_two_families_do_not_share_the_ceiling(make_rig: MakeRig, clock: FixedClock) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    seed_spend(rig.factory, rig.brand_id, "eval_item:CHEIO", spent="1.500000", now=clock.now())
    result = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:E1")))
    assert result.outcome == "ok"
    blocked = specialized(rig).produce(request_for(rig, PackageTriggers.single("eval_item:CHEIO")))
    assert blocked.outcome == "teto_pacote"


def test_concurrent_spec_use_does_not_leak_a_cap_between_requests(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig()
    seed_spend(rig.factory, rig.brand_id, "eval_item:A", spent="1.400000", now=clock.now())
    spec = load_content_specs().designer
    guards = [guard_for(rig, "eval_item:A"), guard_for(rig, "eval_item:B")]
    capped: list[AgentSpec] = []
    threads = [threading.Thread(target=lambda g=g: capped.append(g.cap(spec))) for g in guards]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(item.limits.max_cost_usd for item in capped) == [Decimal("0.1"), Decimal("0.5")]
    assert spec.limits.max_cost_usd == Decimal("0.50")


# --- percurso habitual ------------------------------------------------------------------------


def test_generate_post_fails_without_retry_when_the_ceiling_runs_out_before_a_package(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    seed_spend(
        rig.factory, rig.brand_id, f"post.review:{job.id}", spent="1.470000", now=clock.now()
    )

    assert rig.pipeline.generate(job) == "drafting_failed"

    stored, post = rig.job(job.id), rig.post(post_id)
    assert stored.status == "failed" and stored.last_error == COST_CEILING_REASON
    assert (post.lifecycle, post.last_error) == ("drafting", COST_CEILING_REASON)
    assert rig.revisions(post_id) == [] and rig.generated_requests() == 0


def test_generate_post_ships_the_last_complete_package_when_the_ceiling_runs_out(
    make_rig: MakeRig,
) -> None:
    script = package_pass("rever", ["imagem_inadequada"], "Mal.") + fake_scenarios.visual_draft()
    rig = make_rig(script, image_price="0.04", package_max_cost_usd=Decimal("0.05"))
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    assert rig.pipeline.generate(job) == "in_review"

    [revision] = rig.revisions(post_id)
    assert revision.review_notes == f"Mal.\nrevisao final indisponivel: {COST_CEILING_NOTE}"
    assert revision.final_asset_id is not None and revision.review_verdict == "rever"
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal("0.04"),) * 2
    post = rig.post(post_id)
    assert (post.lifecycle, post.last_error) == ("in_review", None)
    assert rig.job(job.id).status == "succeeded"


def test_generate_post_runs_under_the_three_post_triggers(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    job = rig.claimed(rig.seed_post())
    assert rig.pipeline.generate(job) == "in_review"
    with rig.factory() as session:
        found = set(session.scalars(select(AgentRun.trigger)).all())
    assert found == set(PackageTriggers.for_job(job.id).family)


def test_generate_post_applies_the_default_ceiling_to_every_run(
    make_rig: MakeRig, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    job = rig.claimed(rig.seed_post())
    # 1.30 ja gastos por uma tentativa anterior do job: com o teto por omissao (1.50) sobram 0.20.
    trigger = PackageTriggers.for_job(job.id).design
    seed_spend(rig.factory, rig.brand_id, trigger, spent="1.300000", now=clock.now())
    spy = SpecSpy([])
    spy.install(rig, monkeypatch)

    assert rig.pipeline.generate(job) == "in_review"

    assert [role for role, _ in spy.seen] == ["copywriter", "designer", "revisor"]
    assert [limit for _, limit in spy.seen] == [Decimal(v) for v in ("0.20", "0.20", "0.16")]
    assert DEFAULT_PACKAGE_MAX_COST_USD == CEILING


def test_the_worker_pipeline_is_built_with_the_configured_ceiling(
    make_rig: MakeRig, clock: FixedClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@127.0.0.1:5433/db",
        package_max_cost_usd=Decimal("1.40"),
        media_dir=tmp_path / "media",
    )
    pipeline = _build_pipeline(settings, rig.factory, rig.engine, rig.store, rig.queue, rig.clock)
    job = rig.claimed(rig.seed_post())
    seed_spend(
        rig.factory, rig.brand_id, PackageTriggers.for_job(job.id).design,
        spent="1.300000", now=clock.now(),
    )  # fmt: skip
    spy = SpecSpy([])
    spy.install(rig, monkeypatch)

    assert pipeline.generate(job) == "in_review"

    assert spy.seen[0] == ("copywriter", Decimal("0.10"))  # 1.40 do Settings, nao 1.50


def test_a_new_job_for_the_same_post_starts_with_a_fresh_ceiling(
    make_rig: MakeRig, clock: FixedClock
) -> None:
    rig = make_rig(package_pass(), image_price="0.04")
    post_id = rig.seed_post()
    old = rig.claimed(post_id)
    seed_spend(rig.factory, rig.brand_id, f"post.copy:{old.id}", spent="1.500000", now=clock.now())
    assert rig.pipeline.generate(old) == "drafting_failed"  # teto do job antigo esgotado
    new = rig.claimed(post_id, "changes")
    assert rig.pipeline.generate(new) == "in_review"  # o pedido novo e outra familia


@pytest.mark.parametrize(
    "trigger", ["post.copy:J1", "generate_post:J1", "", "eval_item:", "eval_item", "EVAL_ITEM:1"]
)
def test_single_trigger_must_be_an_eval_item_trigger(trigger: str) -> None:
    with pytest.raises(ValueError, match="eval_item") as caught:
        PackageTriggers.single(trigger)
    assert str(caught.value) == "o trigger unico deve ser 'eval_item:<id>'"  # mensagem fixa
