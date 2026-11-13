"""Fixtures dos testes da comparacao: rig do conteudo com a media da marca."""

from collections.abc import Callable
from pathlib import Path

import pytest
from content_rig import ContentRig, Factory, Script, build_rig

from marketing_agent.clock import FixedClock

MakeRig = Callable[..., ContentRig]


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> MakeRig:
    """Constroi o rig (pipeline real sobre cliente falso) com o guiao dado."""

    def make(script: Script | None = None, **options: object) -> ContentRig:
        return build_rig(
            session_factory,
            clock,
            brand_id,
            script or [],
            media_dir=tmp_path / "media",
            **options,  # type: ignore[arg-type]
        )

    return make
