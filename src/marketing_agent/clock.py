"""Relogio injetavel: todas as implementacoes devolvem datetimes com fuso UTC."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Fonte de tempo. `now()` devolve sempre um datetime com fuso UTC."""

    def now(self) -> datetime:
        """Devolve o instante atual, com fuso UTC."""
        ...


class SystemClock:
    """Relogio real do sistema."""

    def now(self) -> datetime:
        """Devolve o instante atual, com fuso UTC."""
        return datetime.now(UTC)


class FixedClock:
    """Relogio controlado, para testes deterministas."""

    def __init__(self, at: datetime) -> None:
        """Fixa o instante inicial; rejeita datetimes sem fuso (naive)."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError(f"FixedClock exige datetime com fuso, recebido naive: {at!r}")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        """Devolve o instante fixado, com fuso UTC."""
        return self._at

    def advance(self, delta: timedelta) -> None:
        """Avanca (ou recua, com delta negativo) o instante fixado."""
        self._at = self._at + delta
