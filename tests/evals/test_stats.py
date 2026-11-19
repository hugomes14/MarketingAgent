"""Intervalo de Wilson em casos conhecidos."""

import pytest

from marketing_agent.evals.stats import wilson_interval


def test_wilson_6_of_8() -> None:
    assert wilson_interval(6, 8) == pytest.approx((0.4093, 0.9285), abs=1e-4)


def test_wilson_0_of_8() -> None:
    assert wilson_interval(0, 8) == pytest.approx((0.0, 0.3244), abs=1e-4)


def test_wilson_8_of_8_is_symmetric_to_0_of_8() -> None:
    low, high = wilson_interval(8, 8) or (0, 0)
    assert (low, high) == pytest.approx((1 - 0.3244, 1.0), abs=1e-4)


def test_wilson_zero_trials_is_none() -> None:
    assert wilson_interval(0, 0) is None


@pytest.mark.parametrize(("successes", "trials", "z"), [(-1, 3, 1.96), (4, 3, 1.96), (1, 3, 0)])
def test_wilson_rejects_invalid_counts(successes: int, trials: int, z: float) -> None:
    with pytest.raises(ValueError):
        wilson_interval(successes, trials, z)
