"""Wilson a 95% em casos conhecidos e contagens invalidas."""

import math

import pytest

from marketing_agent.evals.stats import wilson_interval


def test_wilson_6_of_8():
    assert wilson_interval(6, 8) == pytest.approx((0.4093, 0.9285), abs=1e-4)


def test_wilson_0_of_8():
    assert wilson_interval(0, 8) == pytest.approx((0.0, 0.3244), abs=1e-4)


def test_wilson_zero_trials_is_none():
    assert wilson_interval(0, 0) is None


@pytest.mark.parametrize(
    "wins,total,z",
    [
        (-1, 8, 1.96),
        (9, 8, 1.96),
        (0, -1, 1.96),
        (0, 8, 0),
        (1, 8, math.inf),
        (1, 8, math.nan),
        (True, 8, 1.96),
        (1.5, 8, 1.96),
    ],
)
def test_wilson_rejects_invalid_counts(wins, total, z):
    with pytest.raises(ValueError):
        wilson_interval(wins, total, z)
