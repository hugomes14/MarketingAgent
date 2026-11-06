"""Enderecos de loopback: o painel e o proxy de desenvolvimento nunca ficam expostos."""

import pytest

from marketing_agent.loopback import is_loopback_host, parse_loopback_endpoint


@pytest.mark.parametrize("host", ["127.0.0.1", "127.9.9.9", "::1", "localhost", "[::1]"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "100.64.0.1", "example.com", ""])
def test_non_loopback_hosts(host: str) -> None:
    assert not is_loopback_host(host)


def test_parse_endpoint_ipv4() -> None:
    assert parse_loopback_endpoint("127.0.0.1:8001", "--listen") == ("127.0.0.1", 8001)


def test_parse_endpoint_ipv6_and_localhost() -> None:
    assert parse_loopback_endpoint("[::1]:8001", "--listen") == ("::1", 8001)
    assert parse_loopback_endpoint("localhost:80", "--listen") == ("127.0.0.1", 80)


@pytest.mark.parametrize(
    "text", ["8001", "127.0.0.1", "127.0.0.1:abc", "127.0.0.1:0", "127.0.0.1:70000", ":80"]
)
def test_parse_endpoint_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError, match="--listen"):
        parse_loopback_endpoint(text, "--listen")


@pytest.mark.parametrize("text", ["0.0.0.0:8001", "192.168.1.5:8001", "example.com:80"])
def test_parse_endpoint_rejects_non_loopback(text: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        parse_loopback_endpoint(text, "--listen")
