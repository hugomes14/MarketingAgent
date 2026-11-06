"""Registo de validadores de saida dos agentes, referenciados pelo nome nos specs."""

from collections.abc import Callable

from pydantic import BaseModel

# Um validador recebe a saida ja tipada e devolve as violacoes encontradas (vazio = ok).
Validator = Callable[[BaseModel], list[str]]

# Vazio por agora: as Tarefas 11 e 12 registam aqui os validadores reais.
VALIDATORS: dict[str, Validator] = {}
