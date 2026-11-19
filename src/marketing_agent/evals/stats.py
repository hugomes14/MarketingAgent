"""Estatistica do relatorio da avaliacao: o intervalo de Wilson de uma proporcao binomial."""

import math
from typing import Final

Z_95: Final = 1.96


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float] | None:
    """Intervalo de Wilson para `successes` em `trials`; `None` sem ensaios.

    Escolhido pelo bom comportamento com amostras pequenas e proporcoes perto de 0 ou de 1
    (o intervalo de Wald colapsa para um ponto com 0 de 8 ou 8 de 8).

    Raises:
        ValueError: contagens negativas, `successes > trials` ou `z <= 0`.
    """
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("contagens invalidas: exige-se 0 <= sucessos <= ensaios")
    if z <= 0:
        raise ValueError("z tem de ser positivo")
    if trials == 0:
        return None
    share = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (share + z2 / (2 * trials)) / denominator
    half = z * math.sqrt(share * (1 - share) / trials + z2 / (4 * trials * trials)) / denominator
    return max(0.0, center - half), min(1.0, center + half)
