from pathlib import Path
from typing import cast

import yaml

COMPOSE_PATH = Path(__file__).resolve().parent.parent / "compose.test.yaml"


def test_postgres_published_on_loopback_only() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    compose = cast(dict[str, dict[str, dict[str, object]]], yaml.safe_load(text))
    postgres = compose["services"]["postgres"]
    ports = cast(list[str], postgres["ports"])
    assert ports
    assert all(port.startswith("127.0.0.1:") for port in ports)
    healthcheck = cast(dict[str, list[str]], postgres["healthcheck"])
    assert any("-h 127.0.0.1" in part for part in healthcheck["test"])


def test_dev_postgres_is_persistent_and_loopback_only() -> None:
    path = COMPOSE_PATH.with_name("compose.dev.yaml")
    compose = cast(dict[str, dict[str, dict[str, object]]], yaml.safe_load(path.read_text("utf-8")))
    postgres = compose["services"]["postgres"]
    assert postgres["container_name"] == "marketing_agent_dev_postgres"
    assert postgres["ports"] == ["127.0.0.1:5434:5432"]
    assert "tmpfs" not in postgres
    volumes = cast(list[str], postgres["volumes"])
    assert volumes and volumes[0].split(":")[0] in compose["volumes"]
    healthcheck = cast(dict[str, list[str]], postgres["healthcheck"])
    assert any("-h 127.0.0.1" in part for part in healthcheck["test"])
