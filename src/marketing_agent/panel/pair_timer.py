"""Tempo gasto a avaliar um par, medido pelo servidor com um token assinado.

A pagina do par leva um campo escondido `inicio` com o instante em que foi servida, assinado
(HMAC-SHA256) com o segredo do painel e ligado a corrida e ao par. Ao gravar, o servidor calcula
os segundos a partir do seu proprio relogio: o browser nao consegue inventar o tempo nem
reaproveitar o token de outro par. Pausas longas contam ate `MAX_SECONDS` (o limite da coluna).
"""

import hashlib
import hmac
from datetime import datetime
from typing import Final

MAX_SECONDS: Final = 3600
# Tolerancia a pequenas diferencas de relogio entre emitir e receber (o mesmo processo).
_CLOCK_SKEW_S: Final = 5


class PairTimer:
    """Emite e verifica os tokens de inicio de avaliacao de um par."""

    def __init__(self, secret: bytes) -> None:
        """`secret` e o segredo do painel (o mesmo do CSRF), com dominio proprio na assinatura."""
        self._secret = secret

    def issue(self, run_id: str, index: int, now: datetime) -> str:
        """Token `<epoch>.<assinatura>` para o par `index` da corrida."""
        stamp = str(int(now.timestamp()))
        return f"{stamp}.{self._sign(run_id, index, stamp)}"

    def seconds(self, token: str, run_id: str, index: int, now: datetime) -> int:
        """Segundos desde a emissao (0 a `MAX_SECONDS`); `ValueError` se o token nao vale."""
        stamp, _, signature = token.partition(".")
        if not stamp.isdigit() or not signature:
            raise ValueError("token de tempo mal formado")
        expected = self._sign(run_id, index, stamp)
        if not hmac.compare_digest(expected, signature):
            raise ValueError("token de tempo invalido")
        elapsed = int(now.timestamp()) - int(stamp)
        if elapsed < -_CLOCK_SKEW_S:
            raise ValueError("token de tempo no futuro")
        return min(max(elapsed, 0), MAX_SECONDS)

    def _sign(self, run_id: str, index: int, stamp: str) -> str:
        message = f"eval-pair-timer:{run_id}:{index}:{stamp}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()
