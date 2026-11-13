"""CLI de operacao: marca, precos, reservas, migracoes, painel e worker."""

import json
import logging
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent import cli
from marketing_agent.brands import ensure_brand
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import ModelPrice, PriceBook, PriceTier, sync_model_prices
from marketing_agent.clock import Clock, SystemClock
from marketing_agent.db.base import make_engine, make_session_factory
from marketing_agent.db.models import AgentRun, Brand, CostReservation
from marketing_agent.dev_proxy import DevProxyServer
from marketing_agent.jobs.worker import Worker
from marketing_agent.startup import load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
Factory = sessionmaker[Session]


@pytest.fixture(autouse=True)
def cli_environment(monkeypatch: pytest.MonkeyPatch, pg_url: str, tmp_path: Path) -> Iterator[None]:
    """Sem `.env` (cwd vazio) e com a base de teste: o CLI le tudo do ambiente."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    yield


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_create_brand_idempotent(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "create-brand", "Padaria Teste")
    assert code == 0
    brand_id = out.strip()
    code, again, _ = run(capsys, "create-brand", "Padaria Teste")
    assert code == 0 and again.strip() == brand_id
    with session_factory() as session:
        rows = session.scalars(select(Brand)).all()
    assert [(row.id, row.name) for row in rows] == [(brand_id, "Padaria Teste")]


def test_create_brand_with_different_name_is_refused(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "create-brand", "A")
    code, out, err = run(capsys, "create-brand", "B")
    assert code == 2 and out == ""
    assert err.strip() == "erro: instalacao de marca unica: ja existe a marca 'A'"
    with session_factory() as session:
        assert [row.name for row in session.scalars(select(Brand))] == ["A"]


def test_concurrent_create_brand_same_name_makes_one_row(
    session_factory: Factory, pg_url: str
) -> None:
    engine = make_engine(pg_url)
    factory = make_session_factory(engine)
    barrier = threading.Barrier(2)

    def create() -> tuple[str, bool]:
        barrier.wait()
        return ensure_brand(factory, "Concorrente")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
    finally:
        engine.dispose()
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Brand)) == 1


def test_concurrent_create_brand_different_names_make_one_row(
    session_factory: Factory,
) -> None:
    barrier = threading.Barrier(2)

    def create(name: str) -> int:
        barrier.wait()
        return cli.main(["create-brand", name])

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(create, ["Primeira", "Segunda"]))
    assert codes == [0, 2]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Brand)) == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_create_brand_rejects_bad_names(
    name: str, session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "create-brand", name)
    assert code == 2 and err.startswith("erro:") and "nome" in err


def test_sync_prices_loads_repo_file(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = REPO_ROOT / "config" / "model_prices.yaml"
    code, out, _ = run(capsys, "sync-prices", "--file", str(path))
    assert code == 0 and out.strip() == "6 precos sincronizados"
    price = PriceBook(session_factory).get("anthropic", "claude-haiku-5-5")
    assert price.price_version == "2026-11-05"
    code, out, _ = run(capsys, "sync-prices", "--file", str(path))  # repetir e no-op
    assert code == 0 and out.strip() == "6 precos sincronizados"


def test_sync_prices_default_path_comes_from_settings(
    monkeypatch: pytest.MonkeyPatch, session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODEL_PRICES_PATH", str(REPO_ROOT / "config" / "model_prices.yaml"))
    code, out, _ = run(capsys, "sync-prices")
    assert code == 0 and "6 precos" in out


def test_sync_prices_several_files(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    config = REPO_ROOT / "config"
    code, out, _ = run(
        capsys,
        "sync-prices",
        "--file",
        str(config / "model_prices.yaml"),
        "--file",
        str(config / "model_prices.local.yaml"),
    )
    assert code == 0 and out.strip() == "10 precos sincronizados"
    assert PriceBook(session_factory).get("openai", "qwen2.5:14b").tiers[0].input_per_million == 0
    vision = PriceBook(session_factory).get("openai", "qwen2.5vl:7b")
    assert vision.price_version == "local-2026-11-09"
    assert (vision.tiers[0].input_per_million, vision.tiers[0].output_per_million) == (0, 0)


def test_sync_prices_missing_file_is_a_config_error(
    session_factory: Factory, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, err = run(capsys, "sync-prices", "--file", str(tmp_path / "nao_existe.yaml"))
    assert code == 2 and out == "" and "nao_existe.yaml" in err and "Traceback" not in err


def test_sync_prices_changed_values_are_refused(
    session_factory: Factory, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = (REPO_ROOT / "config" / "model_prices.yaml").read_text(encoding="utf-8")
    run(capsys, "sync-prices", "--file", str(REPO_ROOT / "config" / "model_prices.yaml"))
    changed = tmp_path / "alterado.yaml"
    changed.write_text(source.replace('"0.75"', '"0.80"'), encoding="utf-8")
    code, _, err = run(capsys, "sync-prices", "--file", str(changed))
    assert code == 2 and "imutavel" in err


def seed_open_reservation(factory: Factory, brand_id: str, clock: Clock, age: timedelta) -> str:
    with factory.begin() as session:
        run_row = AgentRun(
            brand_id=brand_id,
            role="diretor",
            spec_version=1,
            trigger="test",
            status="running",
            started_at=clock.now() - age,
        )
        session.add(run_row)
        session.flush()
        reservation = CostReservation(
            brand_id=brand_id,
            run_id=run_row.id,
            role="diretor",
            provider="fake",
            model_id="m1",
            price_version="v1",
            reserved_usd=Decimal("0.5"),
            status="open",
            created_at=clock.now() - age,
        )
        session.add(reservation)
        session.flush()
        return reservation.id


def test_recover_reservations_marks_unknown(
    session_factory: Factory, brand_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = SystemClock()
    old = seed_open_reservation(session_factory, brand_id, clock, timedelta(minutes=30))
    recent = seed_open_reservation(session_factory, brand_id, clock, timedelta(minutes=1))
    code, out, _ = run(capsys, "recover-reservations", "--older-than-minutes", "10")
    assert code == 0 and out.strip() == "1 reserva(s) marcada(s) como unknown_outcome"
    with session_factory() as session:
        assert session.get_one(CostReservation, old).status == "unknown_outcome"
        assert session.get_one(CostReservation, recent).status == "open"
    status = BudgetController(
        session_factory, PriceBook(session_factory), clock, Decimal(20), (50,)
    ).status(brand_id)
    assert status.unknown_usd == Decimal("0.5")


@pytest.mark.parametrize("minutes", ["0", "-5", "abc"])
def test_recover_reservations_rejects_bad_minutes(
    minutes: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "recover-reservations", "--older-than-minutes", minutes)
    assert code == 2 and "older-than-minutes" in err


def test_migrate_is_idempotent_on_migrated_database(
    session_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "migrate")
    assert code == 0 and "head" in out and err == ""
    with session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM alembic_version")) == 1


def test_migrate_finds_repository_files() -> None:
    root = cli.find_repository_root()
    assert (root / "alembic.ini").is_file() and (root / "migrations").is_dir()


def test_migrate_reports_missing_repository_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "find_repository_root", lambda: tmp_path)
    code, _, err = run(capsys, "migrate")
    assert code == 2 and "alembic.ini" in err


def test_missing_database_url_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    code, _, err = run(capsys, "create-brand", "X")
    assert code == 2 and "DATABASE_URL" in err and "Traceback" not in err


def test_invalid_settings_error_never_shows_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MONTHLY_BUDGET_REFERENCE_USD", "valor-secreto-xyz")
    code, _, err = run(capsys, "create-brand", "X")
    assert code == 2 and "MONTHLY_BUDGET_REFERENCE_USD" in err and "valor-secreto-xyz" not in err


def test_unexpected_error_is_exit_1_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object) -> int:
        raise ZeroDivisionError("detalhe")

    monkeypatch.setitem(cli.COMMANDS, "create-brand", boom)
    code, _, err = run(capsys, "create-brand", "X")
    assert code == 1 and "ZeroDivisionError" in err and "Traceback" not in err


@pytest.mark.parametrize("argv", [[], ["nao-existe"], ["create-brand"], ["serve", "--port", "abc"]])
def test_usage_errors_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, *argv)
    assert code == 2 and err != ""


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == 0 and "create-brand" in out and "dev-proxy" in out


# --- serve -----------------------------------------------------------------------------------


def test_uvicorn_config_never_rewrites_proxy_headers() -> None:
    config = cli.build_uvicorn_config("127.0.0.1", 8000)
    assert config.proxy_headers is False
    assert config.factory is True
    assert config.app == "marketing_agent.panel.main:create_app_from_env"
    assert config.server_header is False
    assert (config.host, config.port) == ("127.0.0.1", 8000)


def test_serve_refuses_when_forwarded_allow_ips_is_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[uvicorn.Config] = []
    monkeypatch.setattr(cli, "run_server", started.append)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    code, _, err = run(capsys, "serve")
    assert code == 2 and "FORWARDED_ALLOW_IPS" in err and started == []


def test_serve_refuses_empty_but_defined_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[uvicorn.Config] = []
    monkeypatch.setattr(cli, "run_server", started.append)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    code, _, _ = run(capsys, "serve")
    assert code == 2 and started == []


def test_serve_refuses_non_loopback_host_without_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[uvicorn.Config] = []
    monkeypatch.setattr(cli, "run_server", started.append)
    code, _, err = run(capsys, "serve", "--host", "0.0.0.0")
    assert code == 2 and "--allow-non-loopback" in err and started == []


def test_serve_allows_non_loopback_with_flag_and_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[uvicorn.Config] = []
    monkeypatch.setattr(cli, "run_server", started.append)
    code, _, err = run(capsys, "serve", "--host", "0.0.0.0", "--allow-non-loopback")
    assert code == 0 and "aviso" in err and len(started) == 1 and started[0].host == "0.0.0.0"


def test_serve_defaults_to_loopback_8000(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[uvicorn.Config] = []
    monkeypatch.setattr(cli, "run_server", started.append)
    code, _, _ = run(capsys, "serve")
    assert code == 0
    assert (started[0].host, started[0].port, started[0].proxy_headers) == (
        "127.0.0.1",
        8000,
        False,
    )


def test_serve_startup_error_is_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Sem PANEL_CSRF_SECRET_FILE o factory falha; o lancador traduz para a mensagem curta.
    monkeypatch.setenv("ROLE_ROUTES", "{}")
    code, _, err = run(capsys, "serve", "--port", str(free_port()))
    assert code == 2 and "PANEL_CSRF_SECRET_FILE" in err and "Traceback" not in err


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- dev-proxy -------------------------------------------------------------------------------


def test_dev_proxy_refuses_non_loopback_listen(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "dev-proxy", "--login", "dono@local", "--listen", "0.0.0.0:8001")
    assert code == 2 and "loopback" in err


def test_dev_proxy_refuses_invalid_login(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "dev-proxy", "--login", "a\nb")
    assert code == 2 and "login" in err


def test_dev_proxy_requires_login(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "dev-proxy")
    assert code == 2 and "--login" in err


def test_dev_proxy_prints_banner_and_serves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: list[tuple[str, int]] = []

    def fake_serve(server: object) -> None:
        served.append(getattr(server, "server_address"))  # noqa: B009
        getattr(server, "server_close")()  # noqa: B009

    monkeypatch.setattr(cli, "serve_forever", fake_serve)
    port = free_port()
    code, out, err = run(
        capsys, "dev-proxy", "--login", "dono@local", "--listen", f"127.0.0.1:{port}"
    )
    assert code == 0 and served == [("127.0.0.1", port)]
    assert "dono@local" in out + err and "DESENVOLVIMENTO" in (out + err)


def test_dev_proxy_upstream_timeout_follows_the_settings_multiplier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    timeouts: list[float] = []

    def fake_serve(server: DevProxyServer) -> None:
        timeouts.append(server.upstream_timeout)
        server.server_close()

    monkeypatch.setattr(cli, "serve_forever", fake_serve)
    monkeypatch.setenv("LLM_TIME_MULTIPLIER", "5")
    code, _, _ = run(
        capsys, "dev-proxy", "--login", "dono@local", "--listen", f"127.0.0.1:{free_port()}"
    )
    assert code == 0 and timeouts == [1863.0]  # 240 x 5 + 600 + 63


# --- worker ----------------------------------------------------------------------------------

WORKER_ROUTES = {
    role: {
        "provider": "openai",
        "model_id": "modelo",
        "capabilities": ["text", "json_schema", *(["image_input"] if role == "revisor" else [])],
    }
    for role in ("diretor", "copywriter", "designer", "revisor")
}


@pytest.fixture
def worker_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session_factory: Factory
) -> dict[str, str]:
    """Rotas, preco e chave de pacotilha: o worker constroi o cliente mas nunca chama a rede."""
    free = PriceTier(0, Decimal(0), Decimal(0))
    price = ModelPrice(
        "openai", "modelo", "v1", (free,), valid_from=datetime(2026, 11, 1, tzinfo=UTC)
    )
    sync_model_prices(session_factory, [price])
    key_file = tmp_path / "openai.key"
    key_file.write_text("chave-de-teste")
    values = {
        "ROLE_ROUTES": json.dumps(WORKER_ROUTES),
        "OPENAI_API_KEY_FILE": str(key_file),
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
        "MEDIA_DIR": str(tmp_path / "media"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_worker_stops_cleanly_on_signal_in_process(
    sent: signal.Signals,
    session_factory: Factory,
    worker_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = Worker.run_forever
    handlers_before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def signal_then_run(self: Worker, stop: threading.Event) -> None:
        # Garante que o worker ja instalou o seu tratador: sem ele o sinal mataria o pytest.
        assert signal.getsignal(sent) not in (signal.SIG_DFL, signal.default_int_handler)
        signal.raise_signal(sent)
        original(self, stop)

    monkeypatch.setattr(Worker, "run_forever", signal_then_run)
    code, out, err = run(capsys, "worker")
    assert code == 0 and out == "" and "Traceback" not in err
    assert {sig: signal.getsignal(sig) for sig in handlers_before} == handlers_before


def read_line_with_timeout(stream: object, timeout_s: float) -> str:
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)  # type: ignore[arg-type]
        assert selector.select(timeout_s), "o worker nao arrancou a tempo"
    return stream.readline()  # type: ignore[attr-defined]


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_worker_cli_stops_cleanly(
    sent: signal.Signals, session_factory: Factory, worker_environment: dict[str, str]
) -> None:
    code = "import sys; from marketing_agent.cli import main; sys.exit(main(['worker']))"
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env={**os.environ, **worker_environment},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # O aviso dos fundos simulados e visivel no arranque (sem rota `designer_imagem`).
        assert "fundos simulados: sem rota designer_imagem" in read_line_with_timeout(
            process.stderr, 30
        )
        assert read_line_with_timeout(process.stderr, 30).startswith("worker a escutar")
        process.send_signal(sent)
        process.wait(timeout=15)
        stderr = process.stderr.read() if process.stderr else ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert process.returncode == 0
    assert "Traceback" not in stderr and process.stdout is not None
    assert process.stdout.read() == ""


def test_worker_requires_copywriter_designer_and_reviewer_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ROLE_ROUTES", "{}")
    code, _, err = run(capsys, "worker")
    assert code == 2 and "Traceback" not in err
    assert err.strip() == "erro: ROLE_ROUTES: faltam as rotas de copywriter, designer, revisor"


def test_worker_refuses_a_missing_designer_route(
    worker_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # O pacote corre sempre o Designer: sem rota o worker nao arranca (nada e faturado).
    routes = {role: route for role, route in WORKER_ROUTES.items() if role != "designer"}
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    code, out, err = run(capsys, "worker")
    assert code == 2 and out == "" and "Traceback" not in err
    assert err.strip() == "erro: ROLE_ROUTES: faltam as rotas de designer"


def test_worker_warns_about_simulated_backgrounds_without_an_image_route(
    session_factory: Factory,
    worker_environment: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="marketing_agent.cli")
    cli.build_worker(load_settings(), session_factory)
    assert "fundos simulados: sem rota designer_imagem" in caplog.text


def test_worker_refuses_a_text_only_reviewer_route(
    worker_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    routes = {
        **WORKER_ROUTES,
        "revisor": {**WORKER_ROUTES["revisor"], "capabilities": ["text", "json_schema"]},
    }
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    code, out, err = run(capsys, "worker")
    assert code == 2 and out == "" and "Traceback" not in err
    assert err.strip() == (
        "erro: ROLE_ROUTES: a rota 'revisor' precisa das capacidades ['image_input']; "
        "ver .env.local.example"
    )


def test_worker_refuses_a_route_without_price(
    worker_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    routes = {**WORKER_ROUTES, "revisor": {**WORKER_ROUTES["revisor"], "model_id": "sem-preco"}}
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    code, _, err = run(capsys, "worker")
    assert code == 2 and "Traceback" not in err
    assert err.strip() == (
        "erro: ROLE_ROUTES: a rota 'revisor' (openai/sem-preco) nao tem preco; "
        "correr 'marketing-agent sync-prices'"
    )


def test_worker_refuses_fake_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = {
        role: {"provider": "fake", "model_id": "m", "capabilities": ["text", "json_schema"]}
        for role in ("copywriter", "designer", "revisor")
    }
    monkeypatch.setenv("ROLE_ROUTES", json.dumps(routes))
    code, _, err = run(capsys, "worker")
    assert code == 2 and "fake" in err and "Traceback" not in err


@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
def test_first_signal_restores_default_handlers_so_a_second_one_forces_exit(
    sent: signal.Signals,
) -> None:
    stop = threading.Event()
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    with cli.stop_on_signals(stop):
        assert signal.getsignal(sent) not in before.values()
        signal.raise_signal(sent)
        assert stop.is_set()
        assert {sig: signal.getsignal(sig) for sig in before} == before  # logo apos o 1o sinal


def test_worker_second_sigint_interrupts_with_exit_130(
    session_factory: Factory,
    worker_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted(self: Worker, stop: threading.Event) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Worker, "run_forever", interrupted)
    code, _, err = run(capsys, "worker")
    assert code == 130 and "Traceback" not in err
