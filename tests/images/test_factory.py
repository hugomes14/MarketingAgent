"""Fabrica do gerador de imagem: rota `designer_imagem` -> OpenAI; sem rota -> falso."""

from pathlib import Path

import pytest

from marketing_agent.config import IMAGE_ROUTE, RoleRoute, Settings
from marketing_agent.images.factory import ImageBackend, build_image_backend
from marketing_agent.images.generation import FAKE_IMAGE_MODEL, FakeImageGenerator
from marketing_agent.images.openai_images import OpenAiImageGenerator

_SEGREDO = "sk-imagens-segredo-de-teste"


def _image_route(model_id: str = "modelo-da-rota") -> RoleRoute:
    return RoleRoute.model_validate(
        {"provider": "openai", "model_id": model_id, "capabilities": {"image_output"}}
    )


def _settings(routes: dict[str, RoleRoute], **kwargs: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://x", role_routes=routes, **kwargs)  # type: ignore[arg-type]


def test_backend_defaults_to_fake_without_route() -> None:
    backend = build_image_backend(_settings({}))
    assert isinstance(backend, ImageBackend)
    assert isinstance(backend.generator, FakeImageGenerator)
    assert (backend.provider, backend.model_id, backend.quality) == (
        "fake",
        FAKE_IMAGE_MODEL,
        "medium",
    )


def test_backend_uses_configured_quality() -> None:
    assert build_image_backend(_settings({}, image_quality="high")).quality == "high"


def test_backend_builds_openai_generator_from_route_and_key_file(tmp_path: Path) -> None:
    key = tmp_path / "images.key"
    key.write_text(_SEGREDO, encoding="utf-8")
    settings = _settings(
        {IMAGE_ROUTE: _image_route("modelo-x")},
        openai_images_api_key_file=key,
        image_quality="low",
    )
    backend = build_image_backend(settings)
    assert isinstance(backend.generator, OpenAiImageGenerator)
    assert (backend.provider, backend.model_id, backend.quality) == ("openai", "modelo-x", "low")
    assert _SEGREDO not in repr(backend)


@pytest.mark.parametrize("mode", ["unset", "missing", "empty"])
def test_backend_requires_images_key_file(tmp_path: Path, mode: str) -> None:
    key_file: Path | None = None
    if mode == "missing":
        key_file = tmp_path / "nao-existe.key"
    elif mode == "empty":
        key_file = tmp_path / "vazio.key"
        key_file.write_text("  \n", encoding="utf-8")
    settings = _settings({IMAGE_ROUTE: _image_route()}, openai_images_api_key_file=key_file)
    with pytest.raises(ValueError, match="OPENAI_IMAGES_API_KEY_FILE") as info:
        build_image_backend(settings)
    assert _SEGREDO not in str(info.value)
    assert (info.value.__cause__ is None) == (mode == "unset")


def test_text_key_file_is_not_used_for_images(tmp_path: Path) -> None:
    text_key = tmp_path / "text.key"
    text_key.write_text(_SEGREDO, encoding="utf-8")
    settings = _settings({IMAGE_ROUTE: _image_route()}, openai_api_key_file=text_key)
    with pytest.raises(ValueError, match="OPENAI_IMAGES_API_KEY_FILE"):
        build_image_backend(settings)
