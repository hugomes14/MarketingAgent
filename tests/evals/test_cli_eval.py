"""Confirmacao obrigatoria antes de enfileirar uma avaliacao."""

import io
from contextlib import contextmanager

import pytest
from eval_rig import MakeEvalRig

from marketing_agent import cli, cli_eval
from marketing_agent.config import Settings


@pytest.fixture
def eval_cli(make_eval_rig: MakeEvalRig, monkeypatch: pytest.MonkeyPatch, pg_url: str):
    rig = make_eval_rig()

    @contextmanager
    def database(settings):
        yield rig.factory

    monkeypatch.setattr(cli_eval, "load_settings", lambda: Settings(database_url=pg_url))
    monkeypatch.setattr(cli_eval, "open_database", database)
    monkeypatch.setattr(cli_eval, "build_cli_runs", lambda settings, factory: rig.service)
    return rig


def test_cli_eval_run_requires_confirmation(eval_cli, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("sim\n"))
    assert cli.main(["eval", "run", "--set", "conj"]) == 2
    captured = capsys.readouterr()
    assert "6.00" in captured.out and "--yes" in captured.err
    assert eval_cli.content.llm.requests == []
    assert cli.main(["eval", "run", "--set", "conj", "--yes"]) == 0
    assert "Corrida criada:" in capsys.readouterr().out


@pytest.mark.parametrize("answer,created", [("sim", True), ("nao", False), ("", False)])
def test_cli_eval_tty_confirms_estimate(eval_cli, monkeypatch, capsys, answer, created):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal(answer + "\n"))
    assert cli.main(["eval", "run", "--set", "conj"]) == 0
    text = capsys.readouterr().out
    assert "4 pacotes" in text and "referencia" in text
    assert ("Corrida criada:" in text) == created
    assert eval_cli.content.llm.requests == []
