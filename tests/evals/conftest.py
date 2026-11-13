"""Fixtures dos testes da comparacao: rig do conteudo com a media da marca."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import ContentRig, Factory, Script, build_rig
from eval_rig import CEILING, EvalRig, MakeEvalRig, build_eval_rig

from marketing_agent.clock import FixedClock
from marketing_agent.jobs.queue import JobQueue

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


@pytest.fixture
def make_eval_rig(make_rig: MakeRig, tmp_path: Path) -> MakeEvalRig:
    """Rig do conteudo mais o servico de corridas.

    `script` e `options` vao para o rig base; `briefs`, `ceiling`, `check_routes`, `new_seed` e
    `queue` ajustam o servico (ver `eval_rig.build_eval_rig`).
    """

    def make(
        script: Script | None = None,
        *,
        briefs: Sequence[dict[str, object]] | None = None,
        ceiling: Decimal = CEILING,
        check_routes: Callable[[], None] | None = None,
        new_seed: Callable[[], int] | None = None,
        queue: JobQueue | None = None,
        **options: object,
    ) -> EvalRig:
        return build_eval_rig(
            make_rig(script, **options),
            tmp_path / "evals",
            briefs=briefs,
            ceiling=ceiling,
            check_routes=check_routes,
            new_seed=new_seed,
            queue=queue,
        )

    return make
