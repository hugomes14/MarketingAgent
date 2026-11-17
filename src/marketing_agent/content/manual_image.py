"""Upload manual validado e revisto uma vez, sem gerar nem substituir o texto."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from marketing_agent.images.codec import encode_jpeg, open_bounded, sha256_hex
from marketing_agent.images.layouts import crop_to_portrait
from marketing_agent.media.ingest import NormalizedImage, normalize_photo
from marketing_agent.media.library import MediaError, PhotoOrigin, PhotoSubject


class ManualImage(BaseModel):
    """Declaracoes explicitas do responsavel; IA nao tem valor por omissao."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    # Aparado ANTES de medir: espacos nas pontas nao contam para o limite.
    alt_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
    gerada_por_ia: bool
    origem: PhotoOrigin
    licenca_nota: str | None = Field(default=None, max_length=300)
    mostra: PhotoSubject
    tem_autorizacao_pessoas: bool

    @model_validator(mode="after")
    def validate_authorization(self) -> Self:
        """Recusa pessoas sem autorizacao."""
        if self.mostra == "pessoas" and not self.tem_autorizacao_pessoas:
            raise MediaError()
        return self


def normalize_manual(data: bytes) -> NormalizedImage:
    """Aplica as mesmas fronteiras do upload e recorta a imagem final em 4:5."""
    photo = normalize_photo(data)
    with open_bounded(photo.data, frozenset({"jpeg"})) as image:
        portrait = crop_to_portrait(image)
        try:
            encoded = encode_jpeg(portrait)
        finally:
            portrait.close()
    return NormalizedImage(encoded, "image/jpeg", 1080, 1350, sha256_hex(encoded))
