"""Erros da revisao do responsavel.

Todas as mensagens sao fixas ou feitas so de estados do ciclo de vida, numeros e nomes de
campo: nunca levam o comentario, a nota, a legenda nem ids de outra marca. O painel pode
mostra-las tal como estao.
"""


class ReviewError(Exception):
    """Base dos erros previstos de uma decisao do responsavel."""


class PostNotFound(ReviewError):
    """O post nao existe ou pertence a outra marca (mesma mensagem nos dois casos)."""

    def __init__(self) -> None:
        """Mensagem unica: nao revela se o id existe noutra marca."""
        super().__init__("publicacao inexistente para a marca")


class StaleRevision(ReviewError):
    """A decisao foi tomada sobre uma revisao que ja nao e a mais recente do post."""

    def __init__(self) -> None:
        """Mensagem fixa: o responsavel deve recarregar a pagina."""
        super().__init__("a revisao ja nao e a mais recente; recarregue a pagina")


class InvalidPostState(ReviewError):
    """O estado do post (ou um trabalho em curso) nao permite a decisao."""


class InvalidComment(ReviewError):
    """O comentario do responsavel esta vazio ou excede o maximo."""


class InvalidEdit(ReviewError):
    """O texto editado viola limites ou regras de validacao; `violations` e a lista completa."""

    def __init__(self, summary: str, violations: tuple[str, ...]) -> None:
        """`summary` e a mensagem limitada; `violations` cita campos e contagens, nunca texto."""
        super().__init__(summary)
        self.violations = violations
