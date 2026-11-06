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


def _load(name: str) -> dict[str, object]:
    path = COMPOSE_PATH.with_name(name)
    return cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")))


def test_compose_files_have_distinct_project_names() -> None:
    """Sem `name:` proprio, os dois ficheiros (mesma pasta) partilham o projeto e o servico
    `postgres`, e subir um deles substitui o contentor do outro."""
    dev, test = _load("compose.dev.yaml"), _load("compose.test.yaml")
    assert isinstance(dev.get("name"), str) and isinstance(test.get("name"), str)
    assert dev["name"] == "marketing_agent_dev"
    assert test["name"] == "marketing_agent_test"
    assert dev["name"] != test["name"]


def test_compose_stacks_use_different_loopback_host_ports() -> None:
    def host_ports(compose: dict[str, object]) -> list[str]:
        services = cast(dict[str, dict[str, list[str]]], compose["services"])
        return [port for service in services.values() for port in service["ports"]]

    dev_ports = host_ports(_load("compose.dev.yaml"))
    test_ports = host_ports(_load("compose.test.yaml"))
    assert dev_ports == ["127.0.0.1:5434:5432"] and test_ports == ["127.0.0.1:5433:5432"]
