"""Capacidades das rotas falsas dos testes que correm o Revisor (spec v2 exige `image_input`)."""

from marketing_agent.config import Capability

VISION_CAPABILITIES: frozenset[Capability] = frozenset({"text", "json_schema", "image_input"})
