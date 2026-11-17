"""Estatistica descritiva da comparacao; nao toma decisoes sobre os papeis."""

import math


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float] | None:
    """Intervalo de Wilson; empates nao entram em `trials`. Sem ensaios devolve `None`."""
    if (
        type(successes) is not int
        or type(trials) is not int
        or not 0 <= successes <= trials
        or not math.isfinite(z)
        or z <= 0
    ):
        raise ValueError("contagens ou nivel de confianca invalidos")
    if trials == 0:
        return None
    rate = successes / trials
    denominator = 1 + z * z / trials
    center = (rate + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)
