"""Escolha do gerador de imagens a partir das definicoes."""

from dataclasses import dataclass

from marketing_agent.config import IMAGE_ROUTE, Settings
from marketing_agent.images.generation import (
    FAKE_IMAGE_MODEL,
    FakeImageGenerator,
    ImageGenerator,
    ImageQuality,
)
from marketing_agent.images.openai_images import OpenAiImageGenerator
from marketing_agent.secrets import SecretFileError

_KEY_VARIABLE = "OPENAI_IMAGES_API_KEY_FILE"


@dataclass(frozen=True)
class ImageBackend:
    """Gerador escolhido, com o fornecedor, o modelo e a qualidade a reservar e faturar."""

    generator: ImageGenerator
    provider: str
    model_id: str
    quality: ImageQuality


def build_image_backend(settings: Settings) -> ImageBackend:
    """Rota `designer_imagem` -> gerador OpenAI; sem rota -> gerador falso (custo 0).

    Raises:
        ValueError: se houver rota de imagem e a chave faltar, estiver vazia ou for invalida; a
            mensagem nomeia a variavel `OPENAI_IMAGES_API_KEY_FILE`, nunca o conteudo.
    """
    route = settings.role_routes.get(IMAGE_ROUTE)
    quality = settings.image_quality
    if route is None:
        return ImageBackend(FakeImageGenerator(), "fake", FAKE_IMAGE_MODEL, quality)
    key_file = settings.openai_images_api_key_file
    if key_file is None:
        raise ValueError(f"{_KEY_VARIABLE} nao definido: indique o ficheiro com a chave de imagens")
    try:
        generator = OpenAiImageGenerator(key_file)
    except SecretFileError as exc:
        raise ValueError(f"{_KEY_VARIABLE} invalido: {exc}") from exc
    return ImageBackend(generator, generator.provider, route.model_id, quality)
