"""Testes da construcao do router a partir das definicoes."""

from decimal import Decimal
from pathlib import Path

import pytest

from marketing_agent.config import Capability, RoleRoute, Settings
from marketing_agent.harness.spec import AgentLimits, AgentSpec, LoopType
from marketing_agent.llm.anthropic_adapter import AnthropicClient
from marketing_agent.llm.factory import build_llm_router
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.openai_adapter import OpenAiClient
from marketing_agent.llm.router import LlmRouter, MissingCapability
from marketing_agent.llm.types import LlmClient

_SEGREDO = "sk-segredo-de-teste"


def _route(provider: str, caps: set[Capability] | None = None) -> RoleRoute:
    return RoleRoute.model_validate(
        {"provider": provider, "model_id": "m", "capabilities": caps or {"text"}}
    )


def _key(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(_SEGREDO, encoding="utf-8")
    return path


def _settings(routes: dict[str, RoleRoute], **kwargs: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://x", role_routes=routes, **kwargs)  # type: ignore[arg-type]


def _spec(role: str, required: set[Capability]) -> AgentSpec:
    return AgentSpec(
        role=role,
        name=role,
        version=1,
        loop_type=LoopType.STRUCTURED,
        prompt_file="p.md",
        output_schema="x",
        required_capabilities=frozenset(required),
        reads=[],
        writes=[],
        limits=AgentLimits(max_steps=1, max_tokens=1, max_cost_usd=Decimal("1"), max_duration_s=1),
        max_output_tokens=100,
        budget_share_hint=Decimal("0.1"),
        escalation="nenhuma",
    )


def _clients(router: LlmRouter) -> dict[str, LlmClient]:
    # Atributo privado: o router so expoe `resolve`, e aqui interessa o conjunto construido.
    return dict(router._clients)


def test_router_builds_only_referenced_providers(tmp_path: Path) -> None:
    settings = _settings(
        {"diretor": _route("anthropic")}, anthropic_api_key_file=_key(tmp_path, "a.key")
    )
    # Nao ha chave OpenAI: construir esse cliente falharia, logo nao pode ser construido.
    assert set(_clients(build_llm_router(settings))) == {"anthropic"}


def test_two_roles_two_providers(tmp_path: Path) -> None:
    settings = _settings(
        {"diretor": _route("anthropic"), "revisor": _route("openai")},
        anthropic_api_key_file=_key(tmp_path, "a.key"),
        openai_api_key_file=_key(tmp_path, "o.key"),
    )
    router = build_llm_router(settings)
    assert isinstance(router.resolve(_spec("diretor", {"text"}))[0], AnthropicClient)
    assert isinstance(router.resolve(_spec("revisor", {"text"}))[0], OpenAiClient)


def test_openai_base_url_is_passed_on(tmp_path: Path) -> None:
    settings = _settings(
        {"revisor": _route("openai")},
        openai_api_key_file=_key(tmp_path, "o.key"),
        openai_base_url="http://127.0.0.1:11434/v1",
    )
    client = _clients(build_llm_router(settings))["openai"]
    assert str(client._sdk.base_url).startswith("http://127.0.0.1:11434/v1")


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("anthropic", "ANTHROPIC_API_KEY_FILE"), ("openai", "OPENAI_API_KEY_FILE")],
)
@pytest.mark.parametrize("mode", ["unset", "missing", "empty"])
def test_missing_key_file_names_variable(
    tmp_path: Path, provider: str, variable: str, mode: str
) -> None:
    key_file: Path | None = None
    if mode == "missing":
        key_file = tmp_path / "nao-existe.key"
    elif mode == "empty":
        key_file = tmp_path / "vazio.key"
        key_file.write_text("  \n", encoding="utf-8")
    field = variable.lower()
    settings = _settings({"diretor": _route(provider)}, **{field: key_file})
    with pytest.raises(ValueError, match=variable) as info:
        build_llm_router(settings)
    assert _SEGREDO not in str(info.value)
    assert (info.value.__cause__ is None) == (mode == "unset")


def test_fake_provider_uses_given_client() -> None:
    fake = FakeLlmClient([])
    router = build_llm_router(_settings({"diretor": _route("fake")}), fake=fake)
    assert router.resolve(_spec("diretor", {"text"}))[0] is fake


def test_fake_provider_requires_fake_argument() -> None:
    with pytest.raises(ValueError, match="fake"):
        build_llm_router(_settings({"diretor": _route("fake")}))


def test_client_capabilities_are_union_of_routes(tmp_path: Path) -> None:
    settings = _settings(
        {"a": _route("openai", {"text"}), "b": _route("openai", {"image_input", "json_schema"})},
        openai_api_key_file=_key(tmp_path, "o.key"),
    )
    client = _clients(build_llm_router(settings))["openai"]
    assert client.capabilities == frozenset({"text", "image_input", "json_schema"})


def test_router_enforces_capabilities_per_route(tmp_path: Path) -> None:
    settings = _settings(
        {"a": _route("openai", {"text"}), "b": _route("openai", {"image_input"})},
        openai_api_key_file=_key(tmp_path, "o.key"),
    )
    router = build_llm_router(settings)
    assert router.resolve(_spec("b", {"image_input"}))[1].model_id == "m"
    with pytest.raises(MissingCapability):
        router.resolve(_spec("a", {"image_input"}))
