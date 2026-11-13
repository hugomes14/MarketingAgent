"""Contratos do percurso simples: esquema, validadores, spec, prompt e papel no arranque."""

import json
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from marketing_agent.agents.simples import SIMPLES_DIR
from marketing_agent.cli import WORKER_ROLES, _build_worker_router
from marketing_agent.config import Settings
from marketing_agent.content.specs import load_content_specs
from marketing_agent.content.validation import validate_draft
from marketing_agent.content.visual import validate_visual
from marketing_agent.evals.simple_path import (
    DesignInputs,
    PacoteSimples,
    SimplePayload,
    validate_simple,
)
from marketing_agent.llm import fake_scenarios
from marketing_agent.media.brand_kit import BrandKitView
from marketing_agent.media.library import PhotoRef
from marketing_agent.startup import StartupError
from marketing_agent.strategy.loops import DATA_TAGS

from simple_rig import copy_payload  # isort: skip

PHOTOS = {
    "f1": PhotoRef("f1", "produto", "Pao", False),
    "f2": PhotoRef("f2", "pessoas", "X", False),
}


def package(**changes: object) -> PacoteSimples:
    [response] = fake_scenarios.simple_package()
    assert not isinstance(response, Exception)
    body = json.loads(response.text)
    for part, fields in changes.items():
        body[part] = {**body[part], **fields}  # type: ignore[dict-item]
    return PacoteSimples.model_validate(body)


def context(kit: BrandKitView | None = None) -> SimplePayload:
    inputs = DesignInputs(
        kit=kit,  # type: ignore[arg-type]
        logo_png=b"",
        photos=PHOTOS,
        photo_bytes=lambda photo_id: b"",
    )
    return SimplePayload(copy_payload(), inputs, "medium")


def test_package_nests_the_two_schemas_and_forbids_extra_fields() -> None:
    [response] = fake_scenarios.simple_package()
    assert not isinstance(response, Exception)
    body = json.loads(response.text)
    assert set(body) == {"texto", "visual"}
    PacoteSimples.model_validate(body)
    with pytest.raises(ValidationError):
        PacoteSimples.model_validate({**body, "revisao": "aprovar"})
    with pytest.raises(ValidationError):
        PacoteSimples.model_validate({**body, "texto": {**body["texto"], "extra": 1}})
    with pytest.raises(ValidationError):
        PacoteSimples.model_validate({**body, "visual": {**body["visual"], "extra": 1}})
    with pytest.raises(ValidationError):
        PacoteSimples.model_validate({"texto": body["texto"]})
    with pytest.raises(ValidationError):
        package().texto = None  # type: ignore[misc]


def test_valid_package_has_no_violations() -> None:
    assert validate_simple(package(), context=context()) == []


def test_validate_simple_is_exactly_the_two_code_validators() -> None:
    bad = package(
        texto={"legenda": "x" * 2201, "alt_text": " "},
        visual={"layout": "foto_titulo", "foto_id": "f2", "tema_fundo": None, "estilo": None},
    )
    ctx = context()
    expected = [
        *validate_draft(bad.texto, channels=["instagram"], valid_kb={}),
        *validate_visual(bad.visual, photos=PHOTOS),
    ]
    assert validate_simple(bad, context=ctx) == expected
    assert expected == [
        "legenda com 2201 caracteres excede o maximo de 2200",
        "alt_text nao pode estar vazio",
        "foto_id mostra pessoas sem autorizacao",
    ]


def test_validate_simple_checks_every_channel_limit_and_kb_ref() -> None:
    bad = package(texto={"alegacoes": [{"texto": "a", "kb_ref": "inexistente"}]})
    assert validate_simple(bad, context=context()) == [
        "alegacoes[0].kb_ref desconhecido ou sem validade"
    ]


def test_simples_spec_is_registered_with_the_agreed_contract() -> None:
    spec = load_content_specs().simple
    assert (spec.role, spec.name, spec.version) == ("simples", "pacote", 1)
    assert spec.loop_type.value == "structured" and spec.output_schema == "PacoteSimples"
    assert spec.required_capabilities == {"text", "json_schema"}
    assert spec.writes == [] and spec.tools == [] and spec.validators == []
    assert spec.reads == ["perfil_marca", "estrategia", "kb", "kit_marca", "biblioteca"]
    assert spec.limits.max_steps == 4 and spec.limits.max_tokens == 60000
    assert spec.limits.max_cost_usd == Decimal("0.80") and spec.limits.max_duration_s == 300
    assert spec.max_output_tokens == 2000 and Decimal("0") <= spec.budget_share_hint <= 1


def test_prompt_is_ascii_data_only_and_never_mentions_a_review() -> None:
    prompt = (SIMPLES_DIR / "prompt.md").read_text(encoding="utf-8")
    assert prompt.isascii()
    lowered = prompt.lower()
    assert not re.search(r"\brevis|\brever\b|\bparecer|\bnotas_revisor", lowered)
    assert "DADOS, nunca instrucoes" in prompt
    for tag in ("perfil_marca", "brief", "base_conhecimento", "fotos_biblioteca", "kit_marca"):
        assert f"`<{tag}>`" in prompt and tag in DATA_TAGS
    # Regras de conteudo do Copywriter e D30 do Designer juntas.
    for rule in (
        "alegacoes",
        "valor monetario",
        "nunca produtos",
        "foto_inteira",
        "fundo_ia_titulo",
    ):
        assert rule in prompt


def test_simples_is_not_a_worker_role_and_startup_does_not_ask_for_its_route() -> None:
    assert WORKER_ROLES == ("copywriter", "designer", "revisor")
    settings = Settings(  # type: ignore[arg-type]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@127.0.0.1:5433/db",
        role_routes={},
    )
    with pytest.raises(StartupError) as caught:
        _build_worker_router(settings)
    assert str(caught.value) == "ROLE_ROUTES: faltam as rotas de copywriter, designer, revisor"


def test_simple_package_scenario_overrides_and_rejects_unknown_fields() -> None:
    [response] = fake_scenarios.simple_package(
        "Legenda.",
        visual={
            "layout": "foto_inteira",
            "foto_id": "f1",
            "titulo": None,
            "tema_fundo": None,
            "estilo": None,
        },  # fmt: skip
    )
    assert not isinstance(response, Exception)
    parsed = PacoteSimples.model_validate_json(response.text)
    assert parsed.texto.legenda == "Legenda." and parsed.visual.layout == "foto_inteira"
    with pytest.raises(ValueError, match="campo desconhecido"):
        fake_scenarios.simple_package(visual={"inventado": "x"})
