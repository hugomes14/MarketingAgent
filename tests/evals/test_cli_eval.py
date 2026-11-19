"""`marketing-agent eval run`: estimativa visivel e confirmacao explicita antes de gastar."""

from decimal import Decimal
from pathlib import Path

import pytest
from eval_rig import build_eval_rig

from marketing_agent.cli_eval import confirm_start, format_estimate, run_eval
from marketing_agent.cli_parser import build_parser
from marketing_agent.clock import FixedClock
from marketing_agent.evals.runs import RunEstimate
from marketing_agent.startup import StartupError

ESTIMATE = RunEstimate(
    "briefs_v1", 1, 8, 16, Decimal("1.50"), Decimal("24.00"), Decimal("3.10"), Decimal(20)
)


def test_parser_accepts_eval_run() -> None:
    args = build_parser().parse_args(["eval", "run", "--set", "briefs_v1", "--yes"])
    assert (args.command, args.eval_command, args.set_name, args.yes) == (
        "eval",
        "run",
        "briefs_v1",
        True,
    )


def test_estimate_text_shows_the_upper_bound_and_the_month() -> None:
    text = format_estimate(ESTIMATE)
    assert "16 pacotes" in text and "24.00 USD" in text and "1.50 USD por pacote" in text
    assert "3.10 USD" in text and "referencia 20 USD" in text


def test_cli_eval_run_requires_confirmation() -> None:
    with pytest.raises(StartupError, match="--yes"):
        confirm_start(assume_yes=False, is_interactive=False)
    assert confirm_start(assume_yes=True, is_interactive=False)
    assert confirm_start(assume_yes=False, is_interactive=True, ask=lambda _: "s")
    assert not confirm_start(assume_yes=False, is_interactive=True, ask=lambda _: "")


def test_run_eval_declined_creates_nothing(
    session_factory, clock: FixedClock, brand_id: str, tmp_path: Path,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    rig = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    rig.add_price_item()
    code = run_eval(
        rig.runs, session_factory, "briefs_teste",
        assume_yes=False, is_interactive=True, ask=lambda _: "n",
    )  # fmt: skip
    assert code == 0 and rig.runs.running_run_id(brand_id) is None
    assert "6.00 USD" in capsys.readouterr().out


def test_run_eval_with_yes_starts_and_maps_usage_errors(
    session_factory, clock: FixedClock, brand_id: str, tmp_path: Path,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    rig = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    rig.add_price_item()
    assert (
        run_eval(rig.runs, session_factory, "briefs_teste", assume_yes=True, is_interactive=False)
        == 0
    )
    run_id = rig.runs.running_run_id(brand_id)
    assert run_id is not None and run_id in capsys.readouterr().out
    with pytest.raises(StartupError, match="em curso"):
        run_eval(rig.runs, session_factory, "briefs_teste", assume_yes=True, is_interactive=False)
    with pytest.raises(StartupError, match="nome de conjunto invalido"):
        run_eval(rig.runs, session_factory, "../x", assume_yes=True, is_interactive=False)
