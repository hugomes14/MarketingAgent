"""Temporizador autenticado por par; o browser nao escolhe os segundos gravados."""

import hashlib
import hmac
from datetime import datetime


class PairTimer:
    """Token HMAC vinculado a corrida, indice e instante de abertura do par."""

    def __init__(self, secret: bytes) -> None:
        """Usa o segredo da app, com dominio separado dos tokens CSRF."""
        self._secret = secret

    def _signature(self, run_id: str, index: int, timestamp: str) -> str:
        message = f"eval-pair-v1:{run_id}:{index}:{timestamp}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def issue(self, run_id: str, index: int, now: datetime) -> str:
        """Token sem ids de itens nem qualquer dado sobre o sorteio dos lados."""
        timestamp = str(int(now.timestamp()))
        return f"{timestamp}.{self._signature(run_id, index, timestamp)}"

    def seconds(self, token: str, run_id: str, index: int, now: datetime) -> int:
        """Verifica origem e tempo; recusa futuro e limita a uma hora sem confiar no cliente."""
        if len(token) > 100:
            raise ValueError("temporizador invalido")
        try:
            timestamp, signature = token.split(".")
            if not timestamp.isascii() or not timestamp.isdecimal():
                raise ValueError
            expected = self._signature(run_id, index, timestamp)
            if not hmac.compare_digest(expected, signature):
                raise ValueError
            elapsed = int(now.timestamp()) - int(timestamp)
            if elapsed < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("temporizador invalido") from None
        return min(elapsed, 3600)
