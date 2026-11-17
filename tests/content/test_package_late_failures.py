"""Falhas do Copywriter ou do Revisor a partir da 2.a passagem nao descartam o pacote ja revisto.

O pacote enviado e sempre o da ultima passagem COMPLETA (texto, imagem e parecer da mesma
passagem), com uma nota fixa; nenhum texto hostil do fornecedor chega as notas nem ao `last_error`.
"""

from decimal import Decimal

import pytest
from content_rig import ContentRig, package_pass
from test_package import (
    HOSTILE,
    PRICE,
    MakeRig,
    generation,
    job_id_of,
    make_rig,  # noqa: F401  (fixture)
    request_kinds,
)

from marketing_agent.harness.engine import RunResult, RunStatus
from marketing_agent.harness.run_errors import RunLimitExceeded
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import LlmUnknownOutcome

FIRST_PASS = package_pass("rever", ["voz_incoerente"], "Ajusta a voz.", legenda="Primeira versao")


def assert_first_package_shipped(rig: ContentRig, post_id: str, note: str) -> None:
    """Uma revisao com o texto, a imagem e o parecer da 1.a passagem, mais a nota fixa."""
    [revision] = rig.revisions(post_id)
    assert (revision.caption, revision.review_verdict) == ("Primeira versao", "rever")
    assert revision.final_asset_id is not None
    assert revision.review_notes == f"Ajusta a voz.\n{note}"
    assert HOSTILE not in str(revision.review_notes)
    [final] = rig.assets("final")  # a imagem da mesma passagem: so houve uma composicao
    assert revision.final_asset_id == final.id
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal(PRICE),) * 2
    post = rig.post(post_id)
    assert post.lifecycle == "in_review" and post.last_error is None
    assert rig.job(job_id_of(rig)).status == "succeeded"


def test_copywriter_needs_human_after_a_reviewed_package_ships_it_with_a_fixed_note(
    make_rig: MakeRig,  # noqa: F811
) -> None:
    rig = make_rig([*FIRST_PASS, LlmUnknownOutcome(HOSTILE)], image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review", "copy"]
    assert_first_package_shipped(
        rig, post_id, "revisao final indisponivel: o copywriter pediu ajuda humana"
    )


def test_copywriter_limit_after_a_reviewed_package_ships_it_with_a_fixed_note(
    make_rig: MakeRig,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = make_rig([*FIRST_PASS], image_price=PRICE)
    real_run = rig.engine.run
    copy_runs: list[int] = []

    def run_with_blocked_second_copy(spec, *args, **kwargs):  # type: ignore[no-untyped-def]
        if spec.role == "copywriter":
            copy_runs.append(1)
            if len(copy_runs) == 2:
                return RunResult(RunStatus.BLOCKED_LIMIT, "r", None, HOSTILE, RunLimitExceeded)
        return real_run(spec, *args, **kwargs)

    monkeypatch.setattr(rig.engine, "run", run_with_blocked_second_copy)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert_first_package_shipped(
        rig, post_id, "revisao final indisponivel: limite do copywriter atingido"
    )


def test_copywriter_crash_after_a_reviewed_package_ships_it_with_a_fixed_note(
    make_rig: MakeRig,  # noqa: F811
) -> None:
    rig = make_rig([*FIRST_PASS, RuntimeError(HOSTILE)], image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert_first_package_shipped(rig, post_id, "revisao final indisponivel: falha do copywriter")


def test_reviewer_crash_after_a_reviewed_package_ships_it_with_a_fixed_note(
    make_rig: MakeRig,  # noqa: F811
) -> None:
    script = [
        *FIRST_PASS,
        *fake_scenarios.caption_draft("Segunda versao"),
        *fake_scenarios.visual_draft(),
        RuntimeError(HOSTILE),
    ]
    rig = make_rig(script, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review", "copy", "design", "review"]
    # O texto novo (nao revisto) nunca viaja com a imagem da passagem anterior.
    assert_first_package_shipped(rig, post_id, "revisao final indisponivel: falha do revisor")


def test_first_pass_copywriter_and_reviewer_failures_keep_the_old_behaviour(
    make_rig: MakeRig,  # noqa: F811
) -> None:
    from marketing_agent.content.rounds import AgentRunFailed

    rig = make_rig([RuntimeError(HOSTILE)], image_price=PRICE)
    post_id = rig.seed_post()
    with pytest.raises(AgentRunFailed):
        rig.pipeline.generate(rig.claimed(post_id))
    assert rig.revisions(post_id) == []
