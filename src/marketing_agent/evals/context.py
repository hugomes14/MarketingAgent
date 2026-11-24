"""Contexto de uma corrida de avaliacao, fixado no inicio e guardado em `eval_run`.

Os dois percursos de um brief tem de ver os MESMOS dados (spec 2C, secao 3), mesmo que a base,
a biblioteca, o kit ou o feedback mudem enquanto a corrida espera na fila. Por isso a corrida
guarda uma copia (JSON) do que o Copywriter, o Designer e o simples leem:

- perfil e estrategia: ids dos artefactos (imutaveis) e o conteudo usado (pilares, propostas
  aceites nesse momento);
- base: os itens citados pelos briefs, com o conteudo (um item desativado depois continua);
- fotos: as referencias das fotos ativas (os bytes sao imutaveis e leem-se pelo id);
- kit: o id da versao ativa (as versoes sao imutaveis);
- exemplos de feedback: os que entrariam no prompt nesse momento.

Nada aqui chama o modelo nem escreve na base.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from marketing_agent.agents.diretor.schemas import PropostaOferta
from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package_media import (
    INVALID_KIT_REASON,
    DesignInputs,
    ImageUnavailable,
    PhotoUnavailable,
)
from marketing_agent.db.models import JsonObject, JsonValue
from marketing_agent.feedback.service import FeedbackExampleView
from marketing_agent.kb.service import KbEntry
from marketing_agent.media.brand_kit import BrandKitService, BrandKitView, KitError
from marketing_agent.media.library import AssetNotFound, MediaLibrary, PhotoRef, PhotoSubject
from marketing_agent.media.store import MediaStoreError


@dataclass(frozen=True)
class FixedBrief:
    """Brief do conjunto com os itens da base ja resolvidos para ids."""

    key: str
    tema: str
    pilar: str
    objetivo: str
    canais: tuple[str, ...]
    kb_ids: tuple[str, ...]
    tags: tuple[str, ...]

    def as_post_brief(self) -> dict[str, object]:
        """O brief na forma que o Copywriter, o Designer e o simples recebem num post."""
        return {
            "tema": self.tema,
            "pilar": self.pilar,
            "objetivo": self.objetivo,
            "kb_refs": list(self.kb_ids),
        }

    def to_json(self) -> JsonObject:
        """Forma guardada em `eval_run.briefs`."""
        return {
            "key": self.key,
            "tema": self.tema,
            "pilar": self.pilar,
            "objetivo": self.objetivo,
            "canais": list(self.canais),
            "kb_ids": list(self.kb_ids),
            "tags": list(self.tags),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "FixedBrief":
        """Inverso de `to_json`."""
        return cls(
            key=str(data["key"]),
            tema=str(data["tema"]),
            pilar=str(data["pilar"]),
            objetivo=str(data["objetivo"]),
            canais=tuple(cast(list[str], data["canais"])),
            kb_ids=tuple(cast(list[str], data["kb_ids"])),
            tags=tuple(cast(list[str], data["tags"])),
        )


@dataclass(frozen=True)
class EvalContext:
    """Copia do contexto de escrita e de imagem de uma corrida."""

    profile_artifact_id: str
    perfil_marca: Mapping[str, object]
    strategy_artifact_id: str
    pilares: tuple[str, ...]
    propostas_aceites: tuple[PropostaOferta, ...]
    kb: Mapping[str, KbEntry]
    photos: Mapping[str, PhotoRef]
    kit_id: str
    style_examples: tuple[FeedbackExampleView, ...]
    negative_examples: tuple[FeedbackExampleView, ...]

    def copy_payload(self, brief: FixedBrief) -> CopyPayload:
        """Entrada de escrita de um brief: so os itens da base que ele cita."""
        return CopyPayload(
            brief=brief.as_post_brief(),
            channels=brief.canais,
            perfil_marca=self.perfil_marca,
            pilares=list(self.pilares),
            accepted_proposals=list(self.propostas_aceites),
            kb_entries={kb_id: self.kb[kb_id] for kb_id in brief.kb_ids},
            style_examples=list(self.style_examples),
            negative_examples=list(self.negative_examples),
            reviewer_notes=None,
            owner_comment=None,
        )

    def to_json(self) -> JsonObject:
        """Forma guardada em `eval_run.context_snapshot` (JSON puro)."""
        return {
            "profile_artifact_id": self.profile_artifact_id,
            "perfil_marca": _jsonable(dict(self.perfil_marca)),
            "strategy_artifact_id": self.strategy_artifact_id,
            "pilares": list(self.pilares),
            "propostas_aceites": [p.model_dump(mode="json") for p in self.propostas_aceites],
            "kb": {kb_id: _jsonable(asdict(entry)) for kb_id, entry in self.kb.items()},
            "photos": [_jsonable(asdict(photo)) for photo in self.photos.values()],
            "kit_id": self.kit_id,
            "style_examples": [_jsonable(asdict(e)) for e in self.style_examples],
            "negative_examples": [_jsonable(asdict(e)) for e in self.negative_examples],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "EvalContext":
        """Inverso de `to_json`."""
        kb = cast(dict[str, dict[str, object]], data["kb"])
        photos = cast(list[dict[str, object]], data["photos"])
        return cls(
            profile_artifact_id=str(data["profile_artifact_id"]),
            perfil_marca=cast(dict[str, object], data["perfil_marca"]),
            strategy_artifact_id=str(data["strategy_artifact_id"]),
            pilares=tuple(cast(list[str], data["pilares"])),
            propostas_aceites=tuple(
                PropostaOferta.model_validate(p)
                for p in cast(list[object], data["propostas_aceites"])
            ),
            kb={kb_id: _kb_entry(entry) for kb_id, entry in kb.items()},
            photos={str(p["id"]): _photo(p) for p in photos},
            kit_id=str(data["kit_id"]),
            style_examples=_examples(data["style_examples"]),
            negative_examples=_examples(data["negative_examples"]),
        )


def _jsonable(value: object) -> JsonValue:
    """Converte datas, decimais e tuplos para JSON (recursivo); o resto fica igual."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return cast(JsonValue, value)


def _optional_date(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def _kb_entry(data: Mapping[str, object]) -> KbEntry:
    amount = data["price_amount"]
    return KbEntry(
        id=str(data["id"]),
        kind=str(data["kind"]),
        key=str(data["key"]),
        title=str(data["title"]),
        body=str(data["body"]),
        price_amount=None if amount is None else Decimal(str(amount)),
        price_currency=cast(str | None, data["price_currency"]),
        valid_from=date.fromisoformat(str(data["valid_from"])),
        valid_until=_optional_date(data["valid_until"]),
        source_kind=str(data["source_kind"]),
        source_ref=cast(str | None, data["source_ref"]),
        is_active=bool(data["is_active"]),
    )


def _photo(data: Mapping[str, object]) -> PhotoRef:
    return PhotoRef(
        id=str(data["id"]),
        mostra=cast(PhotoSubject, data["mostra"]),
        descricao=str(data["descricao"]),
        tem_autorizacao_pessoas=bool(data["tem_autorizacao_pessoas"]),
    )


def _examples(raw: object) -> tuple[FeedbackExampleView, ...]:
    return tuple(
        FeedbackExampleView(
            id=str(item["id"]),
            kind=str(item["kind"]),
            before_text=str(item["before_text"]),
            after_text=cast(str | None, item["after_text"]),
            note=cast(str | None, item["note"]),
            expires_at=None
            if item["expires_at"] is None
            else datetime.fromisoformat(str(item["expires_at"])),
            created_at=datetime.fromisoformat(str(item["created_at"])),
            is_active=bool(item["is_active"]),
        )
        for item in cast(Sequence[Mapping[str, object]], raw)
    )


def fixed_design_inputs(
    context: EvalContext, brand_id: str, library: MediaLibrary, kits: BrandKitService
) -> DesignInputs:
    """Media fixada da corrida: o kit gravado, as fotos gravadas e leitura so dessas fotos.

    `ImageUnavailable` (razao fixa) se o kit gravado ja nao existe ou o logotipo falha. As fotos
    leem-se pelo id mesmo que tenham sido desativadas depois do inicio (os bytes sao imutaveis).
    """
    kit = _find_kit(kits, brand_id, context.kit_id)
    try:
        logo = kits.logo_png(brand_id, kit)
    except (KitError, MediaStoreError):
        logo = None
    if logo is None:
        raise ImageUnavailable(INVALID_KIT_REASON)
    photos = dict(context.photos)

    def photo_bytes(photo_id: str) -> bytes:
        if photo_id not in photos:
            raise PhotoUnavailable
        try:
            return library.read_asset(brand_id, photo_id).data
        except (AssetNotFound, MediaStoreError):
            raise PhotoUnavailable from None

    return DesignInputs(kit=kit, logo_png=logo, photos=photos, photo_bytes=photo_bytes)


def _find_kit(kits: BrandKitService, brand_id: str, kit_id: str) -> BrandKitView:
    for kit in kits.history(brand_id):
        if kit.id == kit_id:
            return kit
    raise ImageUnavailable(INVALID_KIT_REASON)
