"""Pacotes da avaliacao pela fila: isolamento do lote, contexto fixo, medidas e recuperacao."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from eval_rig import EvalRig, build_eval_rig
from image_factory import make_jpeg
from sqlalchemy import func, select, update
from test_package_guard import add_cost, add_run

from marketing_agent.clock import FixedClock
from marketing_agent.content.package_costs import run_trigger
from marketing_agent.db.base import new_id
from marketing_agent.db.models import (
    Approval,
    ArtifactHead,
    FeedbackExample,
    Job,
    MediaAsset,
    Post,
    PostRevision,
)
from marketing_agent.jobs.queue import GENERATE_POST_JOB
from marketing_agent.llm import fake_scenarios
from marketing_agent.media.library import PhotoUpload


@pytest.fixture
def rig(session_factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> EvalRig:  # type: ignore[no-untyped-def]
    built = build_eval_rig(session_factory, clock, brand_id, tmp_path / "m")
    built.add_price_item()
    return built


def weekly_counts(rig: EvalRig) -> dict[str, int]:
    with rig.factory() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
            for model in (Post, PostRevision, Approval, FeedbackExample, ArtifactHead)
        }


def test_run_completes_with_four_packages(rig: EvalRig) -> None:
    run_id = rig.start()
    assert rig.drain() == 4
    items = rig.items(run_id)
    assert [i.status for i in items] == ["succeeded"] * 4
    assert rig.run(run_id).status == "ready"
    for item in items:
        snapshot = item.revision_snapshot or {}
        assert item.final_asset_id is not None and snapshot["layout"] == "fundo_ia_titulo"
        review = snapshot["review"]
        if item.path == "simples":
            assert review == {  # type: ignore[comparison-overlap]
                "verdict": None, "violations": [], "notes": None, "image_checked": False,
                "all_violations": [],
            }  # fmt: skip
        else:
            assert review["verdict"] == "aprovar" and review["image_checked"] is True  # type: ignore[index]
    with rig.factory() as session:
        finals = set(session.scalars(select(MediaAsset.id).where(MediaAsset.kind == "final")).all())
    # Bytes iguais (mesmo fundo falso e titulo) reutilizam o ativo: deduplicacao por sha256.
    assert {item.final_asset_id for item in items} <= finals
    assert rig.llm.count("simples") == 2 and rig.llm.count("revisor") == 2


def test_eval_package_never_touches_weekly_tables(rig: EvalRig) -> None:
    before = weekly_counts(rig)
    rig.start()
    rig.drain()
    assert weekly_counts(rig) == before


def test_claim_prefers_generate_post_over_eval_package(rig: EvalRig) -> None:
    rig.start()
    rig.clock.advance(timedelta(seconds=5))  # o job do post e MAIS RECENTE que os da corrida
    with rig.factory.begin() as session:
        rig.queue.enqueue_in(session, GENERATE_POST_JOB, {"post_id": "x"}, f"g:{new_id()}")
    job = rig.queue.claim("w1")
    assert job is not None and job.type == GENERATE_POST_JOB


def test_context_fixed_at_run_start(rig: EvalRig) -> None:
    """Fotos, precos e exemplos criados depois do inicio nao chegam a nenhum percurso."""
    rig.start()
    meta = PhotoUpload(
        mostra="produto", descricao="Foto nova depois do inicio", origem="propria",
        tem_autorizacao_pessoas=False,
    )  # fmt: skip
    rig.library.upload_photo(rig.brand_id, make_jpeg(1200, 1500), meta, "ana")
    rig.add_price_item(key="bolo", amount="3.20")
    rig.drain()
    texts = [request.messages[0].content[0] for _, request in rig.llm.requests]
    joined = "\n".join(getattr(part, "text", "") for part in texts)
    assert "Pao no balcao" in joined  # a foto que existia no inicio
    assert "Foto nova depois do inicio" not in joined
    assert "3.20" not in joined


def test_item_records_cost_latency_invalid_outputs_and_rounds(
    session_factory,
    clock: FixedClock,
    brand_id: str,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    rig = build_eval_rig(
        session_factory, clock, brand_id, tmp_path / "m", text_price_per_million="1"
    )
    rig.add_price_item()
    rig.llm.before = lambda role: clock.advance(timedelta(seconds=2))
    rig.llm.scripts["simples"] += [
        *fake_scenarios.simple_package({"legenda": "x" * 2201}),
        *fake_scenarios.simple_package(),
    ]
    rig.llm.scripts["revisor"] += fake_scenarios.review(
        "rever", ["voz_incoerente"], "Mais simples.", imagem_verificada=True
    )
    run_id = rig.start()
    rig.drain()
    items = rig.items(run_id)
    assert all(item.cost_usd > 0 and (item.latency_ms or 0) >= 2000 for item in items)
    assert sum(item.invalid_outputs for item in items if item.path == "simples") == 1
    rounds = sorted(item.review_rounds for item in items if item.path == "especializado")
    assert rounds == [0, 1]
    voltas = next(i for i in items if i.review_rounds == 1).revision_snapshot or {}
    assert voltas["review"]["all_violations"] == ["voz_incoerente"]  # type: ignore[index]


def test_worker_killed_mid_item_reruns_and_counts_all_cost(rig: EvalRig) -> None:
    """Uma tentativa perdida (worker morto) faturou; o item volta a correr e conta tudo."""
    run_id = rig.start()
    item = rig.items(run_id)[0]
    lost = add_run(rig.factory, rig.brand_id, run_trigger("copy", item.id, "eval"))
    add_cost(rig.factory, rig.brand_id, lost, "0.25")
    job = rig.queue.claim("morto")
    assert job is not None
    rig.clock.advance(timedelta(days=1))  # a lease do worker morto vence
    assert rig.queue.requeue_expired() == 1
    rig.drain()
    item = next(i for i in rig.items(run_id) if i.id == item.id)
    assert item.status == "succeeded" and item.cost_usd == Decimal("0.25")
    assert rig.run(run_id).status == "ready"


def test_dead_eval_job_fails_item_and_run_becomes_ready(rig: EvalRig) -> None:
    run_id = rig.start()
    with rig.factory.begin() as session:
        session.execute(update(Job).values(max_attempts=1))
    rig.llm.scripts["simples"].append(RuntimeError("rede caiu"))
    rig.drain()
    items = rig.items(run_id)
    failed = [i for i in items if i.status == "failed"]
    assert len(failed) == 1 and failed[0].path == "simples"
    assert failed[0].last_error is not None and "rede caiu" not in failed[0].last_error
    assert rig.job(failed[0].job_id or "").status in ("dead", "failed")
    assert rig.run(run_id).status == "ready"


def test_worker_start_reconciles_half_done_run(rig: EvalRig) -> None:
    run_id = rig.start()
    items = rig.items(run_id)
    with rig.factory.begin() as session:
        session.execute(update(Job).where(Job.id == items[0].job_id).values(status="dead"))
    rig.drain()  # os outros tres terminam; o do job morto fica por fechar
    assert rig.run(run_id).status == "running"
    rig.worker.recover()
    item = next(i for i in rig.items(run_id) if i.id == items[0].id)
    assert item.status == "failed" and item.last_error is not None
    assert rig.run(run_id).status == "ready"


def test_missing_kit_version_fails_items_with_fixed_reason(rig: EvalRig) -> None:
    run_id = rig.start()
    run = rig.run(run_id)
    from marketing_agent.db.models import EvalRun

    snapshot = dict(run.context_snapshot)
    snapshot["kit_id"] = new_id()
    with rig.factory.begin() as session:
        session.execute(
            update(EvalRun).where(EvalRun.id == run_id).values(context_snapshot=snapshot)
        )
    rig.drain()
    assert {i.last_error for i in rig.items(run_id)} == {
        "imagem: O kit de marca está indisponível."
    }
    assert rig.run(run_id).status == "ready"
