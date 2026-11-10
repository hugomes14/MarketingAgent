"""Etiquetas em portugues das publicacoes, pareceres e feedback (filtros Jinja do lote).

Valores desconhecidos mostram-se tal como vem (o Jinja escapa-os): um enum novo na base nunca
parte a pagina.
"""

from collections.abc import Callable, Mapping
from typing import Final

from jinja2 import Environment

_LIFECYCLE: Final = {
    "planned": "Por gerar",
    "drafting": "Em geração",
    "in_review": "Por rever",
    "approved": "Aprovada",
    "rejected": "Rejeitada",
    "cancelled": "Cancelada",
}
_OUTCOME: Final = {
    "new": "Nova",
    "rewritten": "Reescrita e gerada de novo",
    "unchanged": "Mantida",
    "restarted": "Mantida e gerada de novo (o trabalho anterior terminou sem texto)",
    "decided_kept": "Mantida (já em revisão ou decidida, igual ao plano)",
    "decided_differs": "Mantida com o texto antigo: o plano mostra outro conteúdo",
    "cancelled": "Cancelada: o lugar sai do plano",
}
_VERDICT: Final = {"aprovar": "Aprovar", "rever": "Rever", "rejeitar": "Rejeitar"}
_VIOLATION: Final = {
    "alegacao_sem_apoio": "alegação sem apoio",
    "preco_sem_validade": "preço sem validade",
    "voz_incoerente": "voz incoerente",
    "fora_do_tema": "fora do tema",
    "limite_canal": "limite do canal",
    "promessa_indevida": "promessa indevida",
    "dados_pessoais": "dados pessoais",
    "instrucao_suspeita": "instrução suspeita",
    "outro": "outro",
}
_DECISION: Final = {
    "approved": "aprovou",
    "rejected": "rejeitou",
    "changes_requested": "pediu alterações",
}
_REVISION_ORIGIN: Final = {"agente": "do agente", "edicao_responsavel": "edição do responsável"}
_FEEDBACK: Final = {
    "estilo": "Estilo: usar como exemplo de tom e de escrita",
    "correcao_factual": "Correção factual: um facto estava errado",
    "temporario": "Temporário: só vale até uma data",
    "negativo": "Negativo: não escrever assim",
    "nao_usar": "Não guardar: não aprender com esta decisão",
}
_FEEDBACK_SHORT: Final = {
    "estilo": "Estilo",
    "correcao_factual": "Correção factual",
    "temporario": "Temporário",
    "negativo": "Negativo",
}


def _labeller(labels: Mapping[str, str]) -> Callable[[str], str]:
    def label(value: str) -> str:
        return labels.get(value, value)

    return label


def install_label_filters(environment: Environment) -> None:
    """Regista os filtros `lifecycle_label`, `verdict_label`, `violation_label`, etc."""
    environment.filters["lifecycle_label"] = _labeller(_LIFECYCLE)
    environment.filters["outcome_label"] = _labeller(_OUTCOME)
    environment.filters["verdict_label"] = _labeller(_VERDICT)
    environment.filters["violation_label"] = _labeller(_VIOLATION)
    environment.filters["decision_label"] = _labeller(_DECISION)
    environment.filters["revision_origin_label"] = _labeller(_REVISION_ORIGIN)
    environment.filters["feedback_label"] = _labeller(_FEEDBACK)
    environment.filters["feedback_kind_label"] = _labeller(_FEEDBACK_SHORT)
