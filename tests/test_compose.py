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
