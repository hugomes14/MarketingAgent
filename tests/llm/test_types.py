"""Tipos do contrato LLM: os bytes de imagem nunca entram numa representacao em texto."""

from marketing_agent.llm.types import ImagePart, LlmRequest, Message, TextPart

JPEG = b"\xff\xd8\xff\xe0SEGREDO-DOS-BYTES"


def test_image_part_repr_hides_bytes_but_equality_uses_them() -> None:
    part = ImagePart("image/jpeg", JPEG)
    assert "SEGREDO" not in repr(part) and "xff" not in repr(part)
    assert "image/jpeg" in repr(part)
    assert part == ImagePart("image/jpeg", JPEG)
    assert part != ImagePart("image/jpeg", JPEG + b"x")


def test_llm_request_repr_has_no_image_bytes() -> None:
    request = LlmRequest(
        model_id="m",
        system="s",
        messages=[Message("user", [TextPart("ola"), ImagePart("image/jpeg", JPEG)])],
        max_output_tokens=10,
    )
    assert "SEGREDO" not in repr(request) and "xff" not in repr(request)
