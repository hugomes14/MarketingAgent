"""Especificacao de um papel de agente, carregada de YAML, e permissoes de escrita."""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from marketing_agent.artifacts.store import ArtifactType
from marketing_agent.config import Capability
from marketing_agent.harness.validators import VALIDATORS

# Campos monetarios: devem ser texto entre aspas no YAML (como em pricing.py).
_MONEY_PATHS = (("budget_share_hint",), ("limits", "max_cost_usd"))


class SpecLoadError(ValueError):
    """Um spec de agente (ou o seu prompt) foi rejeitado; a mensagem cita o ficheiro."""


class PermissionDenied(Exception):
    """O papel tentou escrever um tipo de artefacto que o seu spec nao lista."""


class LoopType(StrEnum):
    """Forma do ciclo de execucao do agente."""

    INTERVIEW = "interview"
    STRUCTURED = "structured"
    RESEARCH = "research"
    PIPELINE = "pipeline"


class AgentLimits(BaseModel):
    """Limites duros de uma execucao; todos estritamente positivos."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    max_cost_usd: Decimal = Field(gt=0)
    max_duration_s: int = Field(gt=0)


class AgentSpec(BaseModel):
    """Contrato de um papel: o que le, o que pode escrever e dentro de que limites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    name: str
    version: int
    loop_type: LoopType
    prompt_file: str
    output_schema: str
    required_capabilities: frozenset[Capability]
    tools: list[str] = []
    reads: list[str]
    writes: list[ArtifactType]
    validators: list[str] = []
    limits: AgentLimits
    max_output_tokens: int
    budget_share_hint: Decimal = Field(ge=0, le=1)
    escalation: str

    def load_prompt(self, base_dir: Path) -> str:
        """Le o prompt do papel; recusa ficheiro em falta ou fora de `base_dir`."""
        return _resolve_inside(base_dir, self.prompt_file).read_text(encoding="utf-8")


def assert_can_write(spec: AgentSpec, type: ArtifactType) -> None:  # noqa: A002
    """Levanta `PermissionDenied` se o papel nao tem `type` na sua lista `writes`."""
    if type not in spec.writes:
        raise PermissionDenied(f"papel {spec.role!r} nao pode escrever artefactos {type.value!r}")


def _resolve_inside(base_dir: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise SpecLoadError(f"prompt_file {relative!r} nao pode ser um caminho absoluto")
    base = base_dir.resolve()
    target = (base / relative).resolve()  # segue symlinks e '..'
    if not target.is_relative_to(base):
        raise SpecLoadError(f"prompt_file {relative!r} sai da pasta {base_dir}")
    if not target.is_file():
        raise SpecLoadError(f"prompt_file {relative!r} nao existe em {base_dir}")
    return target


def _read_mapping(path: Path) -> dict[str, object]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SpecLoadError(f"{path}: nao foi possivel ler o YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecLoadError(f"{path}: o documento deve ser um mapa")
    return data


def _check_money_is_text(data: Mapping[str, object], path: Path) -> None:
    for keys in _MONEY_PATHS:
        node: object = data
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None and not isinstance(node, str):
            raise SpecLoadError(
                f"{path}: {'.'.join(keys)} deve ser texto entre aspas, nao {node!r}"
            )


def _validate_spec(data: dict[str, object], path: Path) -> AgentSpec:
    try:
        return AgentSpec.model_validate(data)
    except ValidationError as exc:
        # include_input=False: nao repetir valores do YAML na mensagem.
        raise SpecLoadError(f"{path}: spec invalido: {exc.errors(include_input=False)}") from exc


def load_agent_spec(path: Path, schemas: Mapping[str, type[BaseModel]]) -> AgentSpec:
    """Carrega e valida um spec YAML; todo o erro e `SpecLoadError` citando `path`."""
    data = _read_mapping(path)
    _check_money_is_text(data, path)
    spec = _validate_spec(data, path)
    if spec.tools:
        raise SpecLoadError(f"{path}: no tools in Plan 01, mas tools={spec.tools}")
    if spec.output_schema not in schemas:
        raise SpecLoadError(f"{path}: output_schema {spec.output_schema!r} desconhecido")
    unknown = [name for name in spec.validators if name not in VALIDATORS]
    if unknown:
        raise SpecLoadError(f"{path}: validadores nao registados: {unknown}")
    try:
        _resolve_inside(path.parent, spec.prompt_file)
    except SpecLoadError as exc:
        raise SpecLoadError(f"{path}: {exc}") from exc
    return spec
