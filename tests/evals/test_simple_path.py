"""Percurso simples (2C): uma chamada, os mesmos validadores, a mesma composicao e o mesmo teto."""

from decimal import Decimal
from pathlib import Path

from content_rig import PROFILE_PAYLOAD, ContentRig, Factory, Script, build_rig
from sqlalchemy import func, select

from marketing_agent.clock import FixedClock
from marketing_agent.content.design import DesignPayload, compose_design
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package_guard import PackageCostExceeded, PackageCostGuard
from marketing_agent.content.package_media import DesignMedia
from marketing_agent.content.rounds import GeneratedPackage
from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.content.visual import RascunhoVisual
from marketing_agent.db.models import AgentRun, AgentStep, Post, PostRevision
from marketing_agent.evals.simple_path import (
    PacoteSimples,
    SimplePath,
    load_simple_spec,
    validate_simple,
)
from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.harness.image_calls import ImageAllowance
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import TextPart

BRIEF = {"tema": "Pao do dia", "pilar": "Producao artesanal", "objetivo": "Mensagens"}


def make_rig(
    factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path, script: Script, **kw: object
) -> ContentRig:
    return build_rig(factory, clock, brand_id, script, media_dir=tmp_path / "m", **kw)  # type: ignore[arg-type]


def copy_payload(rig: ContentRig, channels: tuple[str, ...] = ("instagram",)) -> CopyPayload:
    active = rig.strategy.active_strategy(rig.brand_id)
    assert active is not None
    return CopyPayload(
        brief=BRIEF,
        channels=channels,
        perfil_marca=PROFILE_PAYLOAD,
        pilares=active.estrategia.pilares,
        accepted_proposals=active.propostas_aceites,
        kb_entries={},
        style_examples=[],
        negative_examples=[],
        reviewer_notes="nao deve chegar ao simples",
        owner_comment=None,
    )


def run_simple(
    rig: ContentRig, *, guard: PackageCostGuard | None = None, request_id: str = "item-1"
) -> GeneratedPackage | RunResult:
    path = SimplePath(rig.engine, load_simple_spec(), "medium")
    media = DesignMedia(rig.library, rig.kits).load(rig.brand_id)
    return path.run(
        request_id, rig.brand_id, copy_payload(rig), media, ImageAllowance(2), guard=guard
    )


def test_simple_package_has_text_image_and_no_review(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(session_factory, clock, brand_id, tmp_path, fake_scenarios.simple_package())
    result = run_simple(rig)
    assert isinstance(result, GeneratedPackage)
    assert result.text.verdict is None and result.text.rounds == 0 and not result.image_checked
    assert result.design.is_ai_generated and result.design.final.data.startswith(b"\xff\xd8")
    assert [c.tipo for c in result.design.components] == ["gerada", "logotipo", "texto"]
    with session_factory() as session:
        [run] = session.scalars(select(AgentRun)).all()
    assert (run.role, run.trigger) == ("simples", "eval.simple:item-1")
    assert len(rig.llm.requests) == 1


def test_simple_path_never_creates_posts(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(session_factory, clock, brand_id, tmp_path, fake_scenarios.simple_package())
    assert isinstance(run_simple(rig), GeneratedPackage)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Post)) == 0
        assert session.scalar(select(func.count()).select_from(PostRevision)) == 0


def test_simple_invalid_package_retries_with_validator_message(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    script = [
        *fake_scenarios.simple_package({"legenda": "x" * 2201}),
        *fake_scenarios.simple_package(),
    ]
    rig = make_rig(session_factory, clock, brand_id, tmp_path, script)
    assert isinstance(run_simple(rig), GeneratedPackage)
    assert len(rig.llm.requests) == 2
    retry = rig.llm.requests[1].messages[-1].content[0]
    assert isinstance(retry, TextPart)
    assert "texto: legenda com 2201 caracteres" in retry.text
    with session_factory() as session:
        errors = session.scalars(select(AgentStep.error_class)).all()
    assert "invalid_output" in errors


def test_simple_visual_rules_enforced(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    invented = fake_scenarios.simple_package(
        layout="foto_titulo", foto_id="inventada", tema_fundo=None, estilo=None
    )
    rig = make_rig(session_factory, clock, brand_id, tmp_path, invented * 3)
    result = run_simple(rig)
    assert isinstance(result, RunResult) and result.status is RunStatus.NEEDS_HUMAN
    assert rig.generated_requests() == 0  # o fundo so se gera depois de o pacote validar


def test_validate_simple_combines_both_validators(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(session_factory, clock, brand_id, tmp_path, [])
    photos = DesignMedia(rig.library, rig.kits).load(rig.brand_id).photos
    package = PacoteSimples(
        texto=RascunhoTexto(
            legenda="Ola", hashtags=["sem cardinal"], apelo_a_acao="", alt_text="", alegacoes=[]
        ),
        visual=RascunhoVisual(
            layout="foto_inteira",
            foto_id=None,
            titulo=None,
            tema_fundo=None,
            estilo=None,
            alt_text="Foto.",
            justificacao="Teste.",
        ),
    )
    violations = validate_simple(package, channels=["instagram"], valid_kb={}, photos=photos)
    assert any(v.startswith("texto: ") for v in violations)
    assert any(v.startswith("visual: ") for v in violations)


def test_simple_path_renders_with_same_render_layout(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    """A mesma escolha visual produz os mesmos bytes no simples e no codigo do Designer."""
    rig = make_rig(session_factory, clock, brand_id, tmp_path, [])
    rig.llm.load(
        fake_scenarios.simple_package(
            layout="foto_titulo", foto_id=rig.photo_id, tema_fundo=None, estilo=None
        )
    )
    result = run_simple(rig)
    assert isinstance(result, GeneratedPackage)
    media = DesignMedia(rig.library, rig.kits).load(rig.brand_id)
    payload = DesignPayload(
        brief=BRIEF,
        texto=result.text.draft,
        kit=media.kit,
        logo_png=media.logo_png,
        photos=media.photos,
        photo_bytes=media.photo_bytes,
        image_quality="medium",
        reviewer_notes=None,
        previous_background=None,
        force_new_background=False,
    )
    # Com uma foto nao ha geracao: o contexto do motor nao e usado pela composicao.
    direct = compose_design(None, payload, result.design.draft)  # type: ignore[arg-type]
    assert direct.final.data == result.design.final.data
    assert direct.components == result.design.components


def test_simple_path_respects_package_ceiling(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(
        session_factory, clock, brand_id, tmp_path, fake_scenarios.simple_package(),
        image_price="0.40",
    )  # fmt: skip
    guard = PackageCostGuard(session_factory, brand_id, "item-1", Decimal("0.30"), "eval")
    result = run_simple(rig, guard=guard)
    assert isinstance(result, RunResult) and result.status is RunStatus.BLOCKED_LIMIT
    assert rig.generated_requests() == 0  # a imagem de 0.40 nao cabe nos 0.30 do teto
    assert guard.spent_usd() == 0


def test_simple_path_without_ceiling_left_never_calls_the_model(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(session_factory, clock, brand_id, tmp_path, fake_scenarios.simple_package())

    class Spent(PackageCostGuard):
        def spent_usd(self) -> Decimal:
            return Decimal("0.30")

    guard = Spent(session_factory, brand_id, "item-1", Decimal("0.30"), "eval")
    result = run_simple(rig, guard=guard)
    assert isinstance(result, RunResult) and result.limit is PackageCostExceeded
    assert rig.llm.requests == []


def test_simple_prompt_wraps_all_external_text(
    session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path
) -> None:
    rig = make_rig(session_factory, clock, brand_id, tmp_path, fake_scenarios.simple_package())
    run_simple(rig)
    text = rig.llm.request_text(1)
    for tag in ("perfil_marca", "estrategia", "brief", "base_conhecimento", "kit_marca"):
        assert f"<{tag}>" in text and f"</{tag}>" in text
    assert "<fotos_biblioteca>" in text and "Pao no balcao" in text
    assert "nao deve chegar ao simples" not in text and "<notas_revisor>" not in text
    system = rig.llm.requests[0].system
    assert "revisor" not in system.lower()  # o prompt do simples nao menciona o Revisor
