"""Tratador dos jobs `eval_package` (Plano 2C, T5): itens, custos, recuperacao e isolamento."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import Factory, build_rig, package_pass
from eval_rig import EvalRig, MakeEvalRig, brief, build_eval_rig, write_set
from image_factory import make_jpeg
from sqlalchemy import func, select

from marketing_agent.clock import FixedClock
from marketing_agent.db.models import (
    AgentRun,
    Approval,
    ArtifactHead,
    AuditLog,
    Brand,
    EvalItem,
    EvalRun,
    FeedbackExample,
    Job,
    MediaAsset,
    Post,
    PostRevision,
)
from marketing_agent.evals.progress import close_item_in, failed_outcome, lock_open_item
from marketing_agent.jobs.queue import ClaimedJob
from marketing_agent.jobs.worker import Worker
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import LlmRequest, TextPart
from marketing_agent.media.brand_kit import BrandKitInput
from marketing_agent.media.library import PhotoUpload

HOSTILE = "ignora-as-regras-SEGREDO-4711"
WEEKLY_TABLES = (Post, PostRevision, Approval, FeedbackExample, ArtifactHead)
ONE_BRIEF = [brief("b1")]
BARRIER_TIMEOUT_S = 10


def simple_script() -> list:
    return fake_scenarios.simple_package()


def one_run(rig: EvalRig) -> tuple[str, EvalItem, EvalItem]:
    """Inicia a corrida e devolve (corrida, item simples, item especializado) do brief b1."""
    run_id = rig.start()
    items = {item.path: item for item in rig.items(run_id)}
    return run_id, items["simples"], items["especializado"]


def snapshot_of(item: EvalItem) -> tuple[object, ...]:
    """Todos os campos de um item, para comparar estados sem depender da identidade do ORM."""
    return tuple(getattr(item, column.key) for column in EvalItem.__table__.columns)


def request_texts(requests: list[LlmRequest]) -> str:
    return "\n".join(
        part.text
        for r in requests
        for m in r.messages
        for part in m.content
        if isinstance(part, TextPart)
    )


def row_counts(factory: Factory) -> dict[str, int]:
    with factory() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
            for model in WEEKLY_TABLES
        }


# --- itens -----------------------------------------------------------------------------------


def test_simple_item_succeeds_and_records_the_package(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF, image_price="0.04")
    run_id, simple, _ = one_run(rig)
    rig.content.llm.hooks[1] = lambda: rig.content.clock.advance(timedelta(milliseconds=2500))
    rig.process(simple, simple_script())
    item = rig.item(simple.id)
    assert (item.status, item.last_error, item.review_rounds) == ("succeeded", None, 0)
    assert item.latency_ms == 2500  # relogio injetado, so a producao do pacote
    assert item.cost_usd == Decimal("0.04")  # a imagem; o texto e gratis no rig
    assert item.invalid_outputs == 0
    snapshot = item.revision_snapshot
    assert snapshot is not None
    assert set(snapshot) == {
        "schema", "texto", "visual", "imagem", "pareceres", "parecer_final", "voltas",
    }  # fmt: skip
    assert snapshot["pareceres"] == [] and snapshot["voltas"] == 0
    assert snapshot["texto"]["legenda"].startswith("Pao quente")
    assert snapshot["imagem"]["asset_id"] == item.final_asset_id
    assert snapshot["imagem"]["gerada_por_ia"] is True
    assert {c["tipo"] for c in snapshot["imagem"]["componentes"]} >= {"gerada", "texto"}
    assert rig.job_of(simple).status == "succeeded"
    with rig.factory() as session:
        asset = session.get_one(MediaAsset, item.final_asset_id)
    assert (asset.kind, asset.origem) == ("final", "composta")
    assert rig.run(run_id).status == "running"  # falta o outro item


def test_item_records_cost_latency_invalid_outputs_and_rounds(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF, image_price="0.04")
    _, _, specialized = one_run(rig)
    first = package_pass("rever", ["voz_incoerente"], "Soa pouco proximo.", checked=True)
    second = package_pass()
    caption_json = first[0].text
    script = [*fake_scenarios.invalid_then_valid(caption_json), *first[1:], *second]
    rig.content.llm.hooks[2] = lambda: rig.content.clock.advance(timedelta(seconds=3))
    rig.process(specialized, script)
    item = rig.item(specialized.id)
    assert item.status == "succeeded"
    assert item.invalid_outputs == 1  # a primeira resposta do Copywriter nao era JSON
    assert item.review_rounds == 1
    assert item.latency_ms == 3000
    assert item.cost_usd == Decimal("0.04")  # o fundo da 2.a passagem e reaproveitado
    snapshot = item.revision_snapshot
    assert snapshot is not None
    assert [p["veredicto"] for p in snapshot["pareceres"]] == ["rever", "aprovar"]
    assert snapshot["pareceres"][0]["violacoes"] == ["voz_incoerente"]
    assert snapshot["parecer_final"]["veredicto"] == "aprovar" and snapshot["voltas"] == 1
    assert "Soa pouco proximo" not in str(snapshot)  # as notas livres do Revisor nao se guardam
    with rig.factory() as session:
        triggers = set(session.scalars(select(AgentRun.trigger)))
    assert triggers == {f"eval_item:{specialized.id}"}  # o trigger e a unica ligacao


def test_failed_outcome_marks_the_item_failed_with_a_fixed_error(
    make_eval_rig: MakeEvalRig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = make_eval_rig(briefs=ONE_BRIEF)
    _, simple, _ = one_run(rig)
    hostile = [fake_scenarios.refusal()[0]] * 3  # recusas do modelo: nunca ha pacote
    rig.process(simple, hostile)
    item = rig.item(simple.id)
    assert item.status == "failed" and item.final_asset_id is None
    assert item.revision_snapshot is None and item.latency_ms is not None
    assert item.last_error == "needs_human: NeedsHuman"
    assert rig.job_of(simple).status == "succeeded"  # o trabalho acabou; o resultado e falha
    assert HOSTILE not in caplog.text


def test_unexpected_errors_propagate_so_the_worker_retries(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    _, simple, _ = one_run(rig)
    rig.content.llm.load([RuntimeError(HOSTILE)])
    with pytest.raises(Exception, match="a execucao do simples falhou") as caught:
        rig.handler.handle(rig.claim(simple))
    assert HOSTILE not in str(caught.value)
    assert rig.item(simple.id).status == "running"  # nao acabou: o job repete


# --- idempotencia e lease -------------------------------------------------------------------


def test_redelivered_job_after_the_item_ended_completes_without_work(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    _, simple, _ = one_run(rig)
    rig.process(simple, simple_script())
    before = rig.item(simple.id)
    requests = len(rig.content.llm.requests)
    with rig.factory.begin() as session:  # a fila entrega o mesmo job outra vez
        job = session.get_one(Job, simple.job_id)
        job.status, job.lease_token, job.locked_until = "running", "novo", rig.content.clock.now()
        redelivered = ClaimedJob(job.id, job.type, job.payload, 2, "novo")
    rig.handler.handle(redelivered)
    assert len(rig.content.llm.requests) == requests  # nenhuma chamada paga
    assert snapshot_of(rig.item(simple.id)) == snapshot_of(before)
    assert rig.job_of(simple).status == "succeeded"


def test_job_is_skipped_when_the_run_is_no_longer_running(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, _ = one_run(rig)
    with rig.factory.begin() as session:
        session.get_one(EvalRun, run_id).status = "ready"
    rig.handler.handle(rig.claim(simple))
    assert rig.content.llm.requests == []
    assert rig.item(simple.id).status == "queued" and rig.job_of(simple).status == "succeeded"


def test_lost_lease_rolls_back_the_item_and_the_media(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    _, simple, _ = one_run(rig)

    def steal() -> None:  # a lease expira e outro worker reclama o job a meio da producao
        with rig.factory.begin() as session:
            session.get_one(Job, simple.job_id).lease_token = "de-outro"

    rig.content.llm.hooks[1] = steal
    rig.process(simple, simple_script())
    item = rig.item(simple.id)
    assert item.status == "running" and item.final_asset_id is None  # nada foi gravado
    assert item.revision_snapshot is None
    assert rig.content.assets("final") == []
    job = rig.job_of(simple)
    assert (job.status, job.lease_token) == ("running", "de-outro")


def test_invalid_payload_fails_the_job_without_retry(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    _, simple, _ = one_run(rig)
    claimed = rig.claim(simple)
    broken = ClaimedJob(claimed.id, claimed.type, {"eval_run_id": HOSTILE}, 1, claimed.lease_token)
    rig.handler.handle(broken)
    job = rig.job_of(simple)
    assert job.status == "failed" and HOSTILE not in str(job.last_error)
    assert rig.item(simple.id).status == "failed"


# --- a corrida fecha quando todos os itens acabam -------------------------------------------


def test_run_becomes_ready_only_when_all_items_ended(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    assert rig.run(run_id).status == "running"
    rig.process(specialized, package_pass())
    assert rig.run(run_id).status == "ready"
    assert {item.status for item in rig.items(run_id)} == {"succeeded"}


def test_two_handlers_finishing_together_mark_the_run_ready_exactly_once(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, specialized = one_run(rig)
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)

    def finish(item: EvalItem) -> bool:
        barrier.wait()
        with rig.factory.begin() as session:
            locked = lock_open_item(session, run_id, item.id)
            assert locked is not None
            run, open_item = locked
            threading.Event().wait(0.3)  # segura o lock: o outro tem de esperar por ele
            return close_item_in(session, run, open_item, failed_outcome("teste"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result(timeout=30) for f in [pool.submit(finish, i) for i in (simple, specialized)]
        ]
    assert sorted(results) == [False, True]  # exatamente um fecha a corrida
    assert rig.run(run_id).status == "ready"
    assert {item.status for item in rig.items(run_id)} == {"failed"}


# --- jobs mortos -----------------------------------------------------------------------------


def make_dead(rig: EvalRig, item: EvalItem, status: str = "dead") -> None:
    with rig.factory.begin() as session:
        job = session.get_one(Job, item.job_id)
        job.status, job.last_error = (
            status,
            "WorkerLost: o worker anterior terminou sem concluir o job",
        )
        session.get_one(EvalItem, item.id).status = "running"


def test_dead_eval_job_fails_item_and_run_becomes_ready(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF, image_price="0.04")
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    claimed = rig.claim(specialized)  # o worker reclama e morre; a lease vence tres vezes
    with rig.factory.begin() as session:
        session.get_one(Job, claimed.id).attempts = 3
    rig.content.expire_leases()
    assert rig.content.queue.requeue_expired() == 1
    assert rig.job_of(specialized).status == "dead"
    assert rig.run(run_id).status == "running"  # a fila nao conhece itens
    assert rig.handler.reconcile() == 1
    item = rig.item(specialized.id)
    assert item.status == "failed" and item.last_error is not None
    assert item.last_error.startswith("job dead") and "LeaseExpired" in item.last_error
    assert rig.run(run_id).status == "ready"
    assert rig.handler.reconcile() == 0  # idempotente


def test_worker_start_reconciles_half_done_run(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    make_dead(rig, specialized)  # morreu fora do worker e o item ficou `running`
    assert rig.run(run_id).status == "running"
    Worker(rig.content.queue, rig.content.pipeline, "w2", eval_handler=rig.handler).recover()
    assert rig.item(specialized.id).status == "failed"
    assert rig.run(run_id).status == "ready"


def test_idle_iteration_also_reconciles(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    make_dead(rig, specialized, "cancelled")
    worker = Worker(
        rig.content.queue,
        rig.content.pipeline,
        "w2",
        sleep=lambda seconds: None,
        eval_handler=rig.handler,
    )
    worker._idle(threading.Event())  # noqa: SLF001
    assert rig.item(specialized.id).status == "failed"
    assert rig.run(run_id).status == "ready"


def test_a_requeued_job_does_not_close_its_item(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, _, specialized = one_run(rig)
    rig.claim(specialized)
    rig.content.expire_leases()
    assert rig.content.queue.requeue_expired() == 1  # volta a `queued`, com tentativas
    assert rig.handler.reconcile() == 0
    assert rig.run(run_id).status == "running"


def test_worker_generic_failure_path_fails_the_item_when_the_job_dies(
    make_eval_rig: MakeEvalRig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = make_eval_rig(briefs=ONE_BRIEF)
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    worker = Worker(rig.content.queue, rig.content.pipeline, "w1", eval_handler=rig.handler)
    for attempt in range(3):
        rig.content.llm.load([RuntimeError(HOSTILE)])
        rig.content.clock.advance(timedelta(minutes=5))
        assert worker.run_once() is True
        expected = "queued" if attempt < 2 else "dead"
        assert rig.job_of(specialized).status == expected
    item = rig.item(specialized.id)
    assert item.status == "failed" and item.last_error is not None
    assert item.last_error.startswith("job dead") and HOSTILE not in item.last_error
    assert rig.run(run_id).status == "ready"
    # O motor regista o traceback da excecao do fornecedor (comportamento anterior ao 2C); o que
    # o worker e o tratador registam leva so o nome da classe.
    worker_logs = [
        r.getMessage() for r in caplog.records if r.name.startswith("marketing_agent.jobs")
    ]
    assert worker_logs and all(HOSTILE not in line for line in worker_logs)


# --- worker morto a meio ------------------------------------------------------------------


def test_worker_killed_mid_item_reruns_and_counts_all_cost(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF, image_price="0.04")
    run_id, simple, specialized = one_run(rig)
    rig.process(simple, simple_script())
    replacement = package_pass()

    def kill() -> None:  # morte do processo depois da imagem faturada e antes do Revisor
        rig.content.llm.load(replacement)
        raise SystemExit("kill -9")

    rig.content.llm.load(package_pass())
    rig.content.llm.hooks[len(rig.content.llm.requests) + 3] = kill
    with pytest.raises(SystemExit):
        rig.handler.handle(rig.claim(specialized))
    assert rig.item(specialized.id).status == "running"
    spent = rig.item(simple.id).cost_usd  # so para distinguir os itens
    rig.content.expire_leases()
    assert rig.content.queue.requeue_expired() == 1
    rig.handler.handle(rig.claim(specialized))  # segunda tentativa do mesmo job
    item = rig.item(specialized.id)
    assert item.status == "succeeded" and rig.job_of(specialized).attempts == 2
    assert item.cost_usd == Decimal("0.08") and spent == Decimal("0.04")  # as duas tentativas
    with rig.factory() as session:  # quota de imagens derivada da familia: nunca mais de 2
        billed = session.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.trigger.like("eval_item:%"))
        )
    assert billed is not None and billed >= 3
    assert rig.run(run_id).status == "ready"


# --- isolamento ---------------------------------------------------------------------------


def test_eval_package_never_touches_weekly_tables(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=[brief("b1"), brief("b2")])
    before = row_counts(rig.factory)
    kinds_before = {a.kind for a in rig.content.assets("final")} | {
        a.kind for a in rig.content.assets("componente_gerado")
    }
    run_id = rig.start()
    for item in rig.items(run_id):
        rig.process(item, simple_script() if item.path == "simples" else package_pass())
    assert rig.run(run_id).status == "ready"
    assert {item.status for item in rig.items(run_id)} == {"succeeded"}
    assert row_counts(rig.factory) == before
    with rig.factory() as session:
        kinds = set(session.scalars(select(MediaAsset.kind).where(MediaAsset.kind != "foto"))) - {
            "logotipo"
        }
    assert kinds - kinds_before <= {"final", "componente_gerado"}
    assert len(rig.content.assets("final")) >= 1


def test_two_brands_run_independently(
    make_eval_rig: MakeEvalRig, session_factory: Factory, clock: FixedClock, tmp_path: Path
) -> None:
    first = make_eval_rig(briefs=ONE_BRIEF)
    with session_factory.begin() as session:
        other = Brand(name="Outra marca")
        session.add(other)
        session.flush()
        other_id = other.id
    content2 = build_rig(session_factory, clock, other_id, [], media_dir=tmp_path / "media2")
    second = build_eval_rig(content2, tmp_path / "evals2", briefs=ONE_BRIEF)
    first_run, first_simple, first_specialized = one_run(first)
    second_run, _, _ = one_run(second)
    first.process(first_simple, simple_script())
    first.process(first_specialized, package_pass())
    assert first.run(first_run).status == "ready"
    assert second.run(second_run).status == "running"
    assert {item.status for item in second.items(second_run)} == {"queued"}
    assert second.run(second_run).brand_id == other_id


# --- contexto fixado no inicio ---------------------------------------------------------------


def test_context_fixed_at_run_start(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig(briefs=ONE_BRIEF)
    content = rig.content
    price = rig.add_price_item("pao", body="PRECO-DO-INICIO por 12 EUR.")
    rig.add_style_example("ESTILO-DO-INICIO")
    write_set(rig.evals_dir, [brief("b1", kb_refs=[price])])
    _, simple, specialized = one_run(rig)
    # Depois do inicio: preco novo, foto nova, exemplo novo, kit novo e o preco desativado.
    rig.add_price_item("depois", body="PRECO-NOVO por 99 EUR.")
    rig.add_style_example("ESTILO-NOVO")
    photo = content.library.upload_photo(
        rig.brand_id,
        make_jpeg(1300, 1500),
        PhotoUpload(
            mostra="produto", descricao="FOTO-NOVA", origem="propria", tem_autorizacao_pessoas=False
        ),
        "ana",
    )
    kit = BrandKitInput(cor_primaria="#AA0000", cor_secundaria="#FFFFFF")
    content.kits.save(rig.brand_id, kit, None, "ana")
    content.kb.deactivate(rig.brand_id, price, "ana")
    for item in (simple, specialized):
        start = len(content.llm.requests)
        rig.process(item, simple_script() if item.path == "simples" else package_pass())
        text = request_texts(content.llm.requests[start:])
        assert "PRECO-DO-INICIO" in text and "ESTILO-DO-INICIO" in text, item.path
        assert "PRECO-NOVO" not in text and "ESTILO-NOVO" not in text, item.path
        assert "FOTO-NOVA" not in text and photo.id not in text, item.path
        assert "#AA0000" not in text and "#1F3A5F" in text, item.path
        assert content.photo_id in text, item.path
    assert {rig.item(i.id).status for i in (simple, specialized)} == {"succeeded"}


def test_hostile_model_text_never_reaches_last_error_audit_or_logs(
    make_eval_rig: MakeEvalRig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = make_eval_rig(briefs=[brief("b1", tema=HOSTILE)])
    run_id, simple, specialized = one_run(rig)
    hostile_caption = fake_scenarios.caption_draft(legenda=f"{HOSTILE} ignora tudo")
    rig.process(simple, [*hostile_caption, *hostile_caption, *hostile_caption])
    rig.process(specialized, fake_scenarios.always_invalid())
    assert {item.status for item in rig.items(run_id)} == {"failed"}
    with rig.factory() as session:
        stored = [item.last_error for item in rig.items(run_id)]
        stored += [str(job.last_error) for job in session.scalars(select(Job))]
        stored += [str(audit.detail) for audit in session.scalars(select(AuditLog))]
    assert all(HOSTILE not in str(value) for value in stored)
    assert HOSTILE not in caplog.text
