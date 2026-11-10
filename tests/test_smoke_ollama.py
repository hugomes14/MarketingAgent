"""Partes puras do `scripts/smoke_ollama.py`: argumentos, recusa da base de desenvolvimento,
verificacao do Ollama e relatorio. Nao precisam do Ollama; so a base descartavel usa o Postgres
de teste (porta 5433), nunca a de desenvolvimento.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_ollama.py"
DEV_URL = "postgresql+psycopg://marketing_agent:segredo-dev@127.0.0.1:5434/marketing_agent"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_ollama", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_ollama"] = module  # os dataclasses resolvem anotacoes por este registo
    spec.loader.exec_module(module)
    return module


smoke = load_script()


def test_defaults_use_the_test_server_and_local_models() -> None:
    args = smoke.parse_args([])
    assert args.database_url is None
    assert make_url(args.server_url).port == 5433
    assert (args.model, args.reviewer_model, args.posts) == ("qwen2.5:14b", "llama3.1:8b", 3)
    assert args.ollama_url == "http://127.0.0.1:11434"


def test_help_documents_the_database_safety(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "5434" in out and "marketing_agent" in out and "nunca e apagada" in out


@pytest.mark.parametrize("posts", ["0", "15", "-1", "tres"])
def test_posts_out_of_range_is_a_usage_error(posts: str) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.parse_args(["--posts", posts])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "url",
    [
        DEV_URL,
        "postgresql+psycopg://u:p@127.0.0.1:5434/outra_base",  # porta de desenvolvimento
        "postgresql+psycopg://u:p@localhost:5434/outra_base",
        "postgresql+psycopg://u:p@127.0.0.1:5433/marketing_agent",  # nome da base de dev
        "postgresql+psycopg://u:p@127.0.0.1:5433/marketing_agent_test",  # base do pytest
        "postgresql+psycopg://u:p@db.exemplo.com:5433/smoke",  # fora do loopback
        "postgresql+psycopg://u:p@127.0.0.1:5433",  # sem base
        "isto nao e um url",
    ],
)
def test_unsafe_database_urls_are_refused_without_echoing_the_password(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.parse_args(["--database-url", url])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "recusado" in err or "ilegivel" in err
    assert "segredo-dev" not in err and ":p@" not in err


def test_server_url_on_the_dev_port_is_refused() -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.parse_args(["--server-url", DEV_URL])
    assert exc.value.code == 2


def test_explicit_safe_database_is_accepted() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:5433/marketing_agent_smoke_manual"
    assert smoke.parse_args(["--database-url", url]).database_url == url
    assert smoke.check_database_url(url).database == "marketing_agent_smoke_manual"


def test_throwaway_names_are_random_safe_identifiers() -> None:
    names = {smoke.new_throwaway_name() for _ in range(20)}
    assert len(names) == 20
    assert all(re.fullmatch(r"marketing_agent_smoke_[0-9a-f]{8}", name) for name in names)
    assert not any(name in smoke.PROTECTED_DATABASES for name in names)


def test_throwaway_database_is_created_and_dropped(pg_url: str) -> None:
    server_url = make_url(pg_url).set(database="postgres").render_as_string(hide_password=False)
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")

    def exists(name: str) -> bool:
        with admin.connect() as connection:
            query = text("select 1 from pg_database where datname = :name")
            return connection.execute(query, {"name": name}).first() is not None

    try:
        with smoke.throwaway_database(server_url) as url:
            name = make_url(url).database
            assert name.startswith("marketing_agent_smoke_") and exists(name)
            smoke.check_database_url(url)  # a base descartavel passa a propria verificacao
        assert not exists(name)
        with pytest.raises(RuntimeError), smoke.throwaway_database(server_url) as url:
            name = make_url(url).database
            raise RuntimeError("falha a meio")
        assert not exists(name)  # apagada mesmo quando o percurso falha
    finally:
        admin.dispose()


def test_missing_models_is_exact_and_sorted() -> None:
    available = {"qwen2.5:14b", "nomic-embed-text:latest"}
    assert smoke.missing_models(available, {"qwen2.5:14b", "llama3.1:8b"}) == ["llama3.1:8b"]
    assert smoke.missing_models(available, {"qwen2.5"}) == [
        "qwen2.5"
    ]  # sem correspondencia parcial
    assert smoke.missing_models(available, {"qwen2.5:14b"}) == []


def test_unreachable_ollama_gives_the_fixed_message() -> None:
    with pytest.raises(smoke.OllamaUnavailable) as exc:
        smoke.list_ollama_models("http://127.0.0.1:9")  # porta discard: ligacao recusada
    assert str(exc.value) == smoke.OLLAMA_DOWN_MESSAGE.format(url="http://127.0.0.1:9")


def test_main_exits_3_when_ollama_is_down_and_touches_no_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_: object) -> None:
        raise AssertionError("nao pode criar bases sem o Ollama")

    monkeypatch.setattr(smoke, "throwaway_database", forbidden)
    monkeypatch.setattr(smoke, "run_smoke", forbidden)
    assert smoke.main(["--ollama-url", "http://127.0.0.1:9"]) == smoke.EXIT_OLLAMA
    err = capsys.readouterr().err
    assert "Ollama inacessivel" in err and "nunca o arranca" in err


def test_main_exits_3_when_a_model_is_missing_and_never_pulls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "list_ollama_models", lambda url: {"qwen2.5:14b"})
    monkeypatch.setattr(smoke, "run_smoke", lambda *a: pytest.fail("nao deve correr"))
    assert smoke.main([]) == smoke.EXIT_OLLAMA
    err = capsys.readouterr().err
    assert "llama3.1:8b" in err and "nunca descarrega" in err and "qwen2.5:14b," not in err


def test_local_prices_cover_both_models_at_zero_and_add_unknown_ones() -> None:
    prices = smoke.local_prices({"qwen2.5:14b", "llama3.1:8b", "modelo-novo:7b"})
    by_model = {price.model_id: price for price in prices}
    assert {"qwen2.5:14b", "llama3.1:8b", "modelo-novo:7b"} <= set(by_model)
    for price in by_model.values():
        assert all(
            tier.input_per_million == 0 and tier.output_per_million == 0 for tier in price.tiers
        )


def sample_report() -> object:
    report = smoke.SmokeReport(week="2026-W47")
    report.steps = [
        smoke.StepTiming("entrevista", 12.34, True, "4 respostas"),
        smoke.StepTiming("plano semanal", 40.0, True),
        smoke.StepTiming("worker: job 1", 61.5, True),
    ]
    report.posts = [
        smoke.PostOutcome("2026-W47-1", "in_review", 0, "aprovar", None),
        smoke.PostOutcome("2026-W47-2", "in_review", 2, "rever", None),
    ]
    report.invalid_outputs = {"copywriter": {"invalid_output": 2}, "revisor": {}}
    report.runs = {"copywriter/succeeded": 3, "revisor/succeeded": 4}
    report.cost_entries = 9
    return report


def test_report_lists_time_per_step_rounds_and_invalid_outputs() -> None:
    report = sample_report()
    text_out = smoke.format_report(report)
    assert "2026-W47" in text_out
    assert re.search(r"entrevista\s+12\.3\s+ok \(4 respostas\)", text_out)
    assert re.search(r"worker: job 1\s+61\.5\s+ok", text_out)
    assert re.search(r"total\s+113\.8", text_out)
    assert "2026-W47-2: in_review, voltas do revisor=2, parecer=rever" in text_out
    assert "Voltas do revisor (total): 2" in text_out
    assert "copywriter: invalid_output=2" in text_out
    assert "copywriter/succeeded=3" in text_out and "cost_entry: 9" in text_out
    assert text_out.rstrip().endswith("RESULTADO: OK")
    assert report.is_success


def test_report_flags_failures_and_posts_without_a_review() -> None:
    report = sample_report()
    report.posts[1] = smoke.PostOutcome("2026-W47-2", "drafting", 1, None, "NeedsHuman")
    out = smoke.format_report(report)
    assert "erro=NeedsHuman" in out and out.rstrip().endswith("RESULTADO: FALHOU")
    assert not report.is_success
    failed = sample_report()
    failed.steps.append(smoke.StepTiming("estrategia", 3.0, False))
    assert "FALHOU" in smoke.format_report(failed) and not failed.is_success
    empty = smoke.SmokeReport()
    assert not empty.is_success and "nenhuma" in smoke.format_report(empty)
