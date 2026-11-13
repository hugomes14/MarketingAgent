"""Conjunto de avaliacao versionado da comparacao (spec 2C, secao 4): carregador puro e estrito.

Um conjunto e um ficheiro YAML `<raiz>/<nome>.yaml` com `name`, `version` e 1 a 20 briefs. O
ficheiro e dado externo editavel: o carregador valida tudo na fronteira e nunca ecoa o seu
conteudo. Todas as mensagens de `EvalSetError` sao fixas e so levam nomes de campos e codigos
de erro (nunca valores do ficheiro, nem o nome pedido quando invalido).

O carregador e puro: nao le a base. Que o `pilar` exista na estrategia ativa e que os `kb_refs`
existam na base valida-os quem inicia a corrida (`start_run`), nao este modulo.
"""

import re
import stat
from pathlib import Path
from typing import Annotated, Any, Final, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic_core import PydanticCustomError

from marketing_agent.planning.schemas import Canal, KbRef

SET_NAME_PATTERN: Final = (
    r"^[a-z0-9_]{1,40}$"  # ancorado: o `pattern` do pydantic procura em vez de casar
)
_SET_NAME: Final = re.compile(SET_NAME_PATTERN)
MAX_SET_BYTES: Final = 256 * 1024
MAX_BRIEFS: Final = 20
MAX_KB_REFS: Final = 20
MAX_REPORTED_ERRORS: Final = 5

type EvalTag = Literal["com_preco", "sem_foto_adequada", "promessa_exagerada"]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_ERROR_PREFIX: Final = "conjunto de avaliacao invalido: "


class EvalSetError(ValueError):
    """Conjunto de avaliacao ilegivel ou invalido; a mensagem e fixa e nunca ecoa o ficheiro."""


def _unique[T](value: list[T]) -> list[T]:
    if len(set(value)) != len(value):
        raise PydanticCustomError("repetidos", "valores repetidos")
    return value


class EvalBrief(BaseModel):
    """Um brief do conjunto, com a forma do brief que o pacote recebe (`key` identifica-o)."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key: str = Field(pattern=SET_NAME_PATTERN)
    tema: Annotated[_Text, Field(max_length=300)]
    pilar: Annotated[_Text, Field(max_length=200)]
    objetivo: Annotated[_Text, Field(max_length=300)]
    canais: list[Canal] = Field(min_length=1, max_length=2)
    kb_refs: list[KbRef] = Field(max_length=MAX_KB_REFS)
    tags: list[EvalTag] = Field(max_length=3)

    _unique_lists = field_validator("canais", "kb_refs", "tags")(_unique)


class EvalSet(BaseModel):
    """Conjunto versionado: `name` igual ao ficheiro, `version >= 1` e chaves de brief unicas."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(pattern=SET_NAME_PATTERN)
    version: int = Field(ge=1)
    briefs: list[EvalBrief] = Field(min_length=1, max_length=MAX_BRIEFS)

    @field_validator("briefs")
    @classmethod
    def _keys_are_unique(cls, value: list[EvalBrief]) -> list[EvalBrief]:
        if len({item.key for item in value}) != len(value):
            raise PydanticCustomError("chaves_repetidas", "chaves repetidas")
        return value


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader` sem aliases nem ancoras usadas (anti "billion laughs") e sem chaves repetidas.

    Sem aliases a estrutura nunca cresce acima do tamanho do ficheiro (ja limitado).
    """

    def compose_node(self, parent: Any, index: Any) -> Any:  # noqa: ANN401 - assinatura do PyYAML
        """Recusa qualquer alias antes de o expandir."""
        if self.check_event(yaml.AliasEvent):
            raise EvalSetError(_ERROR_PREFIX + "YAML com aliases ou ancoras")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:  # noqa: ANN401, FBT001, FBT002
        """Recusa chaves repetidas num mapeamento (o PyYAML ficaria com a ultima em silencio)."""
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                marker = (key_node.tag, key_node.value)
                if marker in seen:
                    raise EvalSetError(_ERROR_PREFIX + "YAML com chaves repetidas")
                seen.add(marker)
        return super().construct_mapping(node, deep)


def _resolve_set_file(name: str, root: Path) -> Path:
    """Caminho real de `<root>/<name>.yaml`, garantidamente um ficheiro dentro de `root`."""
    if not isinstance(name, str) or _SET_NAME.fullmatch(name) is None:
        raise EvalSetError("nome de conjunto invalido")
    not_found = EvalSetError("conjunto de avaliacao nao encontrado")
    try:
        real_root = root.resolve(strict=True)
        real_file = (real_root / f"{name}.yaml").resolve(strict=True)
        mode = real_file.stat().st_mode
    except OSError:
        raise not_found from None
    if not real_file.is_relative_to(real_root):
        raise EvalSetError("conjunto de avaliacao fora do diretorio dos conjuntos")
    if not stat.S_ISREG(mode):
        raise not_found
    return real_file


def _read_capped(path: Path) -> bytes:
    """Le o ficheiro ate `MAX_SET_BYTES`; maior que isso e recusado antes de qualquer parse."""
    too_big = EvalSetError("conjunto de avaliacao demasiado grande")
    try:
        if path.stat().st_size > MAX_SET_BYTES:
            raise too_big
        with path.open("rb") as handle:
            data = handle.read(MAX_SET_BYTES + 1)
    except OSError:
        raise EvalSetError("conjunto de avaliacao ilegivel") from None
    if len(data) > MAX_SET_BYTES:
        raise too_big
    return data


def _parse_yaml(data: bytes) -> dict[str, object]:
    """Mapeamento da raiz do YAML (UTF-8, um so documento); qualquer outra coisa e recusada."""
    unreadable = EvalSetError(_ERROR_PREFIX + "YAML ilegivel")
    try:
        # `_StrictLoader` e um `SafeLoader`: nenhuma etiqueta de objeto Python e construida.
        document = yaml.load(data.decode("utf-8"), Loader=_StrictLoader)  # noqa: S506
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError):
        raise unreadable from None
    if not isinstance(document, dict):
        raise EvalSetError(_ERROR_PREFIX + "a raiz tem de ser um mapeamento")
    return document


def _describe_errors(error: ValidationError) -> str:
    """Resumo fixo: campo e codigo do erro, nunca o valor nem o nome de um campo desconhecido."""
    parts: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        loc = list(item["loc"])
        code = item["type"]
        if code == "extra_forbidden":
            loc, code = loc[:-1], "campo desconhecido"
        where = ".".join(str(piece) for piece in loc) or "(raiz)"
        parts.append(f"{where} ({code})")
    shown = "; ".join(parts[:MAX_REPORTED_ERRORS])
    extra = len(parts) - MAX_REPORTED_ERRORS
    return _ERROR_PREFIX + shown + (f"; +{extra} erros" if extra > 0 else "")


def load_eval_set(name: str, root: Path) -> EvalSet:
    """Carrega e valida `<root>/<name>.yaml`; `EvalSetError` com mensagem fixa se algo falhar.

    O nome valida-se (`[a-z0-9_]{1,40}`) antes de tocar no sistema de ficheiros, o ficheiro tem
    de ficar dentro de `root` depois de resolver ligacoes simbolicas, tem no maximo
    `MAX_SET_BYTES` e o `name` interno tem de ser igual ao pedido. Nao consulta a base.
    """
    path = _resolve_set_file(name, root)
    document = _parse_yaml(_read_capped(path))
    try:
        loaded = EvalSet.model_validate(document)
    except ValidationError as error:
        raise EvalSetError(_describe_errors(error)) from None
    if loaded.name != name:
        raise EvalSetError("o campo name do conjunto nao corresponde ao nome pedido")
    return loaded
