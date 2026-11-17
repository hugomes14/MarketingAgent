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


def test_cli_eval_report_writes_markdown_to_docs(eval_cli, monkeypatch, tmp_path, capsys):
    from content_rig import package_pass

    from marketing_agent.evals.blind import JudgementInput
    from marketing_agent.evals.judging import JudgingService
    from marketing_agent.llm.fake_scenarios import simple_package

    monkeypatch.setattr(cli_eval, "report_directory", lambda: tmp_path / "AgentDATA" / "docs")
    run_id = eval_cli.start()
    assert cli.main(["eval", "report", run_id]) == 2
    assert not list(tmp_path.rglob("*relatorio_avaliacao*"))
    for item in eval_cli.items(run_id):
        eval_cli.process(item, simple_package() if item.path == "simples" else package_pass())
    judging = JudgingService(eval_cli.factory, eval_cli.content.clock)
    for index in (1, 2):
        judging.record(
            eval_cli.brand_id,
            run_id,
            index,
            JudgementInput(better="empate", errors_a=[], errors_b=[], edit_a="0", edit_b="0"),
            1,
            None,
            "ana",
        )
    judging.reveal(eval_cli.brand_id, run_id, "ana")
    assert cli.main(["eval", "report", run_id]) == 0
    files = list((tmp_path / "AgentDATA" / "docs").glob("*_relatorio_avaliacao_conj.md"))
    assert len(files) == 1 and "Sem causalidade" in files[0].read_text()
