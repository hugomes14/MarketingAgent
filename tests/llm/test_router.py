"""Testes do router de modelos por papel."""

from collections.abc import Mapping
from decimal import Decimal

import pytest

from marketing_agent.config import Capability, RoleRoute
from marketing_agent.harness.spec import AgentLimits, AgentSpec, LoopType
from marketing_agent.llm.fake import FakeLlmClient
from marketing_agent.llm.router import LlmRouter, MissingCapability, RouteNotConfigured

_TEXT_JSON: frozenset[Capability] = frozenset({"text", "json_schema"})


def _spec(role: str, required: frozenset[Capability] = _TEXT_JSON) -> AgentSpec:
    return AgentSpec(
        role=role,
        name=role,
        version=1,
        loop_type=LoopType.STRUCTURED,
        prompt_file="p.md",
        output_schema="x",
        required_capabilities=required,
        reads=[],
        writes=[],
        limits=AgentLimits(max_steps=1, max_tokens=1, max_cost_usd=Decimal("1"), max_duration_s=1),
        max_output_tokens=100,
        budget_share_hint=Decimal("0.1"),
        escalation="nenhuma",
    )


def _routes() -> Mapping[str, RoleRoute]:
    return {
        "diretor": RoleRoute(provider="fake", model_id="m1", capabilities=_TEXT_JSON),
        "copywriter": RoleRoute(provider="openai", model_id="m2", capabilities=_TEXT_JSON),
    }


def test_router_resolves_per_role() -> None:
    fake_client, openai_client = FakeLlmClient([]), FakeLlmClient([])
    router = LlmRouter(_routes(), {"fake": fake_client, "openai": openai_client})
    client_a, route_a = router.resolve(_spec("diretor"))
    client_b, route_b = router.resolve(_spec("copywriter"))
    assert client_a is fake_client and route_a.model_id == "m1"
    assert client_b is openai_client and route_b.model_id == "m2"


def test_router_missing_route_raises() -> None:
    router = LlmRouter(_routes(), {"fake": FakeLlmClient([])})
    with pytest.raises(RouteNotConfigured, match="estratega"):
        router.resolve(_spec("estratega"))
    with pytest.raises(RouteNotConfigured, match="copywriter.*openai"):
        router.resolve(_spec("copywriter"))


def test_router_missing_capability_raises() -> None:
    router = LlmRouter(_routes(), {"fake": FakeLlmClient([])})
    with pytest.raises(MissingCapability, match="diretor.*image_input"):
        router.resolve(_spec("diretor", frozenset({"text", "image_input"})))
    limited = FakeLlmClient([], capabilities=frozenset({"text"}))
    router = LlmRouter(_routes(), {"fake": limited})
    with pytest.raises(MissingCapability, match="json_schema"):
        router.resolve(_spec("diretor"))
