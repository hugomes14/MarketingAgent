"""Registo de validadores de saida dos agentes, referenciados pelo nome nos specs."""

from collections.abc import Callable

from pydantic import BaseModel

from marketing_agent.agents.diretor.schemas import Diagnostico, Estrategia

# Um validador recebe a saida ja tipada e devolve as violacoes encontradas (vazio = ok).
Validator = Callable[[BaseModel], list[str]]

MIN_PRAZO_DIAS = 1
MAX_PRAZO_DIAS = 90


def _wrong_type(expected: type[BaseModel], got: BaseModel) -> list[str]:
    return [f"esperava {expected.__name__}, recebeu {type(got).__name__}"]


def diagnostico_factos_com_evidencia(output: BaseModel) -> list[str]:
    """Um achado `facto` precisa de evidencia nao vazia e de origem; uma mensagem por achado."""
    if not isinstance(output, Diagnostico):
        return _wrong_type(Diagnostico, output)
    violations = []
    for index, achado in enumerate(output.achados):
        if achado.tipo != "facto":
            continue
        missing = []
        if achado.evidencia is None or not achado.evidencia.strip():
            missing.append("evidencia")
        if achado.origem is None:
            missing.append("origem")
        if missing:
            violations.append(f"achados[{index}]: facto sem {' nem '.join(missing)}")
    return violations


def estrategia_experiencias_completas(output: BaseModel) -> list[str]:
    """Cada experiencia tem hipotese e metrica nao vazias e prazo entre 1 e 90 dias."""
    if not isinstance(output, Estrategia):
        return _wrong_type(Estrategia, output)
    violations = []
    for index, experiencia in enumerate(output.experiencias):
        problems = []
        if not experiencia.hipotese.strip():
            problems.append("hipotese vazia")
        if not experiencia.metrica.strip():
            problems.append("metrica vazia")
        if not MIN_PRAZO_DIAS <= experiencia.prazo_dias <= MAX_PRAZO_DIAS:
            problems.append(f"prazo_dias fora de {MIN_PRAZO_DIAS}..{MAX_PRAZO_DIAS}")
        if problems:
            violations.append(f"experiencias[{index}]: {'; '.join(problems)}")
    return violations


VALIDATORS: dict[str, Validator] = {
    "diagnostico_factos_com_evidencia": diagnostico_factos_com_evidencia,
    "estrategia_experiencias_completas": estrategia_experiencias_completas,
}
