"""Erros previstos do plano semanal; mensagens fixas, sem ids nem texto do plano."""


class PlanningError(Exception):
    """Base dos erros previstos ao aprovar um plano."""


class ConfirmationRequired(PlanningError):
    """Aprovar o plano cancelaria publicacoes `approved` ou `in_review` sem confirmacao."""

    def __init__(self) -> None:
        """Mensagem fixa: o painel diz ao responsavel para marcar a confirmacao."""
        super().__init__("o plano cancelaria publicacoes aprovadas ou em revisao; confirme")
