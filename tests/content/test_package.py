"""Pacote texto e imagem: Designer, Revisor com visao, voltas, ativos, custos e falhas de imagem."""

import hashlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from content_rig import WEEK, ContentRig, Factory, Script, build_rig, package_pass
from image_engine_rig import ScriptedImageGenerator
from sqlalchemy import select, update
from test_pipeline import wait_for_lock_waiter

from marketing_agent.clock import FixedClock
from marketing_agent.config import Capability
from marketing_agent.content.package_costs import remaining_images, run_trigger
from marketing_agent.content.package_media import PHOTO_UNAVAILABLE_MESSAGE, DesignMedia
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.content.rounds import IMAGE_LIMIT_NOTE
from marketing_agent.db.models import AuditLog, CostReservation, Job, MediaAsset, Post
from marketing_agent.harness.run_errors import RunConfigurationFailed
from marketing_agent.images.generation import ImageProviderError
from marketing_agent.jobs.errors import describe_error
from marketing_agent.llm import fake_scenarios
from marketing_agent.llm.types import ImagePart, LlmUnknownOutcome

HOSTILE = "ignora-as-regras-SEGREDO-4711"
PRICE = "0.04"
MakeRig = Callable[..., ContentRig]


@pytest.fixture
def make_rig(session_factory: Factory, clock: FixedClock, brand_id: str, tmp_path: Path) -> MakeRig:
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


def photo_visual(rig: ContentRig, **fields: str | None) -> Script:
    """Designer que escolhe a foto da biblioteca (`foto_titulo`)."""
    base: dict[str, str | None] = {
        "layout": "foto_titulo",
        "foto_id": rig.photo_id,
        "tema_fundo": None,
        "estilo": None,
    }
    return fake_scenarios.visual_draft(**{**base, **fields})


def review(verdict: str = "aprovar", violations: tuple[str, ...] = (), notes: str = "") -> Script:
    return fake_scenarios.review(verdict, violations, notes, imagem_verificada=True)


def generation(rig: ContentRig, post_id: str | None = None) -> tuple[str, str]:
    """Corre um job de geracao do post (novo se nao dado); devolve (resultado, post_id)."""
    post_id = post_id or rig.seed_post()
    return rig.pipeline.generate(rig.claimed(post_id)), post_id


def request_kinds(rig: ContentRig) -> list[str]:
    """Papel de cada pedido ao LLM, pelo esquema do pedido."""
    names = {"RascunhoTexto": "copy", "RascunhoVisual": "design", "Revisao": "review"}
    return [names[(request.json_schema or {})["title"]] for request in rig.llm.requests]


# --- caminho feliz ---------------------------------------------------------------------------


def test_package_happy_path_with_photo_layout(make_rig: MakeRig) -> None:
    rig = make_rig()
    rig.llm.load(
        [
            *fake_scenarios.caption_draft(),
            *photo_visual(rig, titulo="Pao quente"),
            *review(notes="Boa."),
        ]
    )
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review"]
    [revision] = rig.revisions(post_id)
    [final] = rig.assets("final")
    assert (final.origem, final.mime, (final.width, final.height)) == (
        "composta",
        "image/jpeg",
        (1080, 1350),
    )
    assert revision.final_asset_id == final.id
    assert (revision.layout, revision.layout_version) == ("foto_titulo", 1)
    assert revision.is_ai_generated is False and revision.review_image_checked is True
    assert revision.components == [
        {"tipo": "fotografia", "ref": rig.photo_id, "modelo": None, "asset_id": rig.photo_id},
        {"tipo": "logotipo", "ref": rig.kits.active(rig.brand_id).logo_asset_id, "modelo": None,
         "asset_id": rig.kits.active(rig.brand_id).logo_asset_id},
        {"tipo": "texto", "ref": None, "modelo": None, "asset_id": None, "titulo": "Pao quente"},
    ]  # fmt: skip
    # O alt_text da revisao e o do Designer, nao o do Copywriter.
    assert revision.alt_text == "Pao acabado de sair do forno sobre um fundo de aguarela."
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal(0), Decimal(0))
    assert rig.assets("componente_gerado") == [] and rig.generated_requests() == 0
    assert revision.revision_hash == canonical_revision_hash(
        caption=revision.caption,
        hashtags=revision.hashtags,
        call_to_action=revision.call_to_action,
        alt_text=revision.alt_text,
        claims=revision.claims,
        channels=["instagram"],
        final_image_sha256=final.sha256,
    )
    # O Revisor recebeu a imagem final guardada (mesmos bytes).
    [*_, image] = rig.llm.requests[2].messages[0].content
    assert isinstance(image, ImagePart)
    assert image.data == rig.library.read_asset(rig.brand_id, final.id).data


def test_package_with_generated_background_marks_ai_and_cost(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass(), image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    [revision] = rig.revisions(post_id)
    [entry] = rig.image_entries()
    assert entry.units == {"images": 1, "size": "1024x1536", "quality": "medium"}
    assert revision.is_ai_generated is True
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal(PRICE), Decimal(PRICE))
    [background] = rig.assets("componente_gerado")
    assert (background.origem, background.mime) == ("gerada", "image/png")
    generated = next(c for c in revision.components if c["tipo"] == "gerada")
    # `ref` e o id do passo `generate_image`; `asset_id`, o ativo guardado (sao diferentes).
    assert generated["asset_id"] == background.id and generated["ref"] != background.id
    assert generated["modelo"] == "fake-image-1"
    assert entry.step_id == generated["ref"]
    assert [c["tipo"] for c in revision.components] == ["gerada", "logotipo", "texto"]
    assert revision.layout == "fundo_ia_titulo" and revision.review_image_checked is True


# --- voltas ----------------------------------------------------------------------------------


def test_visual_violation_regenerates_background_within_limit(make_rig: MakeRig) -> None:
    script = package_pass("rever", ["imagem_inadequada"], "Fundo pouco adequado.") + [
        *fake_scenarios.visual_draft(),
        *review("aprovar"),
    ]
    rig = make_rig(script, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    # So violacoes visuais: o Copywriter nao volta; o fundo e gerado de novo (mesmo prompt).
    assert request_kinds(rig) == ["copy", "design", "review", "design", "review"]
    assert rig.generated_requests() == 2 and len(rig.image_entries()) == 2
    [revision] = rig.revisions(post_id)
    assert revision.review_verdict == "aprovar" and rig.post(post_id).review_rounds == 1
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal("0.08"),) * 2
    assert len(rig.assets("componente_gerado")) == 1  # o fundo recusado nao fica ligado ao post
    assert "Fundo pouco adequado." in rig.llm.request_text(4)  # o Designer recebe as notas


def test_background_violation_without_quota_ships_the_package_with_a_limit_note(
    make_rig: MakeRig,
) -> None:
    bad = package_pass("rever", ["imagem_inadequada"], "Ainda mal.")
    script = bad + [
        *fake_scenarios.visual_draft(),
        *review("rever", ("imagem_inadequada",), "Ainda mal."),
    ]
    rig = make_rig(script, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review", "design", "review"]  # sem 3.a volta
    assert rig.generated_requests() == 2  # nunca uma 3.a imagem
    [revision] = rig.revisions(post_id)
    assert revision.review_verdict == "rever"
    assert revision.review_violations == ["imagem_inadequada"]
    assert revision.review_notes == f"Ainda mal.\n{IMAGE_LIMIT_NOTE}"
    assert IMAGE_LIMIT_NOTE == "limite de 2 imagens geradas atingido"
    assert rig.post(post_id).lifecycle == "in_review"


def test_visual_only_violation_redoes_only_the_designer_and_reuses_the_background(
    make_rig: MakeRig,
) -> None:
    script = package_pass("rever", ["texto_ilegivel"], "Titulo ilegivel.") + [
        *fake_scenarios.visual_draft(titulo="Outro titulo"),
        *review("aprovar"),
    ]
    rig = make_rig(script, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review", "design", "review"]
    # `texto_ilegivel` nao pede fundo novo: o mesmo prompt reutiliza o fundo, sem custo novo.
    assert rig.generated_requests() == 1 and len(rig.image_entries()) == 1
    [revision] = rig.revisions(post_id)
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal(PRICE), Decimal(PRICE))
    assert len(rig.assets("componente_gerado")) == 1  # nao se guarda um 2.o componente_gerado
    assert len(rig.assets("final")) == 1  # a 1.a imagem final nunca chegou a ser guardada
    generated = next(c for c in revision.components if c["tipo"] == "gerada")
    assert generated["asset_id"] == rig.assets("componente_gerado")[0].id


def test_text_violation_redoes_copy_and_design(make_rig: MakeRig) -> None:
    script = package_pass("rever", ["voz_incoerente"], "Ajusta a voz.") + package_pass(
        legenda="Segunda versao"
    )
    rig = make_rig(script, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review"] * 2
    assert "Ajusta a voz." in rig.llm.request_text(4) and "Ajusta a voz." in rig.llm.request_text(5)
    [revision] = rig.revisions(post_id)
    assert (revision.caption, revision.review_verdict) == ("Segunda versao", "aprovar")
    # O prompt do fundo nao mudou e nenhuma violacao de fundo: reutiliza, uma so imagem faturada.
    assert rig.generated_requests() == 1
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal(PRICE), Decimal(PRICE))
    assert len(rig.assets("componente_gerado")) == 1


def test_mixed_text_and_visual_violations_redo_copy_too(make_rig: MakeRig) -> None:
    script = package_pass("rever", ["texto_ilegivel", "limite_canal"], "Dois problemas.")
    rig = make_rig(script + package_pass())
    assert generation(rig)[0] == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review"] * 2


def test_rounds_capped_at_two_for_package(make_rig: MakeRig) -> None:
    script = (
        package_pass("rever", ["texto_ilegivel"], "NOTA-UM", legenda="Um")
        + [*fake_scenarios.visual_draft(), *review("rever", ("limite_canal",), "NOTA-DOIS")]
        + package_pass("rever", ["marca_incoerente"], "NOTA-TRES", legenda="Tres")
    )
    rig = make_rig(script)
    outcome, post_id = generation(rig)

    assert outcome == "in_review"
    # 1.a volta: so visual (sem Copywriter); 2.a volta: texto (Copywriter + Designer).
    assert request_kinds(rig) == [
        "copy",
        "design",
        "review",
        "design",
        "review",
        "copy",
        "design",
        "review",
    ]
    [revision] = rig.revisions(post_id)
    assert rig.post(post_id).review_rounds == 2
    # A violacao visual da ultima volta segue com as notas para o responsavel.
    assert (revision.review_verdict, revision.review_violations) == ("rever", ["marca_incoerente"])
    assert revision.review_notes == "NOTA-TRES"
    assert rig.job(job_id_of(rig)).status == "succeeded"


def test_aprovar_with_visual_violation_is_treated_as_rever(make_rig: MakeRig) -> None:
    script = package_pass("aprovar", ["alt_text_inadequado"], "Alt fraco.") + [
        *fake_scenarios.visual_draft(),
        *review("aprovar"),
    ]
    rig = make_rig(script)
    assert generation(rig)[0] == "in_review"
    assert request_kinds(rig) == ["copy", "design", "review", "design", "review"]


def test_reviewer_that_did_not_look_at_the_image_fails_the_job_without_a_revision(
    make_rig: MakeRig,
) -> None:
    # O validador do Revisor recusa `imagem_verificada` falso quando ha imagem: 3 pareceres
    # invalidos e o job falha (nunca se grava um parecer que nao viu a imagem).
    rig = make_rig(package_pass(checked=False) + fake_scenarios.review("aprovar") * 2)
    outcome, post_id = generation(rig)
    assert outcome == "drafting_failed" and rig.revisions(post_id) == []
    assert rig.post(post_id).last_error == "NeedsHuman"


# --- falha da parte de imagem ----------------------------------------------------------------


def text_only_revision(
    rig: ContentRig, post_id: str, job_id: str, message: str, attempts: int = 1
) -> None:
    [revision] = rig.revisions(post_id)
    assert revision.review_verdict is None and revision.review_violations == []
    assert revision.review_notes is None and revision.origin == "agente"
    assert revision.final_asset_id is None and revision.layout is None
    assert revision.layout_version is None and revision.components == []
    assert revision.is_ai_generated is False and revision.review_image_checked is False
    assert revision.alt_text == "Pao artesanal acabado de sair do forno."  # o do Copywriter
    post, job = rig.post(post_id), rig.job(job_id)
    assert (post.lifecycle, post.last_error) == ("drafting", message)
    # Sem nova tentativa: `failed` e nao `queued`.
    assert (job.status, job.last_error, job.attempts) == ("failed", message, attempts)
    assert rig.assets("final") == []


def job_id_of(rig: ContentRig) -> str:
    """O unico job do rig."""
    with rig.factory() as session:
        return session.scalars(select(Job.id)).one()


def test_image_blocked_limit_keeps_text_and_leaves_drafting(make_rig: MakeRig) -> None:
    # 0.60 USD por imagem excede o `max_cost_usd` do Designer (0.50): bloqueado antes de chamar.
    rig = make_rig(package_pass(), image_price="0.60")
    outcome, post_id = generation(rig)

    assert outcome == "drafting_failed"
    text_only_revision(rig, post_id, job_id_of(rig), "imagem: limite de custo do designer atingido")
    assert request_kinds(rig) == ["copy", "design"]  # o Revisor nao corre sem imagem
    assert rig.generated_requests() == 0 and rig.image_entries() == []


def test_image_quota_exhausted_in_the_database_blocks_the_designer(make_rig: MakeRig) -> None:
    script = package_pass("rever", ["imagem_inadequada"], "Mal.") + [
        *fake_scenarios.visual_draft(),
        *review("aprovar"),
    ]
    rig = make_rig(script)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[5] = kill_worker  # depois das 2 imagens faturadas, antes do 2.o parecer
    with pytest.raises(SystemExit):
        rig.pipeline.generate(job)
    assert (
        rig.generated_requests() == 2 and remaining_images(rig.factory, rig.brand_id, job.id) == 0
    )

    rig.expire_leases()
    assert rig.queue.requeue_expired() == 1
    retry = rig.queue.claim("w2")
    assert retry is not None
    rig.llm.load(package_pass())
    assert rig.pipeline.generate(retry) == "drafting_failed"

    # O Designer nao chega a chamar o gerador (quota da BD a zero) e o texto segue sozinho,
    # com o custo das 2 imagens da 1.a tentativa registado.
    text_only_revision(rig, post_id, job.id, "imagem: limite de imagens geradas atingido", 2)
    assert rig.generated_requests() == 2
    [revision] = rig.revisions(post_id)
    assert revision.image_cost_usd == revision.package_cost_usd


def kill_worker() -> None:
    raise SystemExit("kill -9")


def test_missing_brand_kit_leaves_drafting_with_reason(make_rig: MakeRig) -> None:
    rig = make_rig(fake_scenarios.caption_draft(), with_kit=False)
    outcome, post_id = generation(rig)

    assert outcome == "drafting_failed"
    text_only_revision(rig, post_id, job_id_of(rig), "imagem: Falta o kit de marca.")
    assert request_kinds(rig) == ["copy"]


def test_unknown_generation_outcome_keeps_text_and_counts_toward_the_quota(
    make_rig: MakeRig,
) -> None:
    generator = ScriptedImageGenerator([LlmUnknownOutcome("ligacao cortada " + HOSTILE)])
    rig = make_rig(package_pass(), generator=generator, image_price=PRICE)
    outcome, post_id = generation(rig)

    assert outcome == "drafting_failed"
    job_id = job_id_of(rig)
    text_only_revision(rig, post_id, job_id, "imagem: a geração ficou com desfecho desconhecido")
    with rig.factory() as session:
        [reservation] = session.scalars(
            select(CostReservation).where(CostReservation.reserved_usd > 0)
        )
    assert reservation.status == "unknown_outcome"
    # Pode ter sido faturada: conta na quota do pedido (mas nao ha lancamento de custo).
    assert remaining_images(rig.factory, rig.brand_id, job_id) == 1
    assert rig.image_entries() == []
    assert HOSTILE not in str(rig.post(post_id).last_error) + str(rig.job(job_id).last_error)


def test_photo_deactivated_before_composition_fails_the_image_with_a_fixed_reason(
    make_rig: MakeRig,
) -> None:
    rig = make_rig()
    rig.llm.load([*fake_scenarios.caption_draft(), *photo_visual(rig, titulo="Pao"), *review()])
    rig.llm.hooks[2] = lambda: rig.library.deactivate(rig.brand_id, rig.photo_id, "ana")
    outcome, post_id = generation(rig)

    assert outcome == "drafting_failed"
    text_only_revision(
        rig, post_id, job_id_of(rig), "imagem: a foto escolhida já não está disponível"
    )


def test_hostile_text_from_the_designer_never_reaches_errors_or_audit(make_rig: MakeRig) -> None:
    generator = ScriptedImageGenerator([ImageProviderError(HOSTILE, 400)])
    rig = make_rig(package_pass(), generator=generator)
    outcome, post_id = generation(rig)

    assert outcome == "drafting_failed"
    job = rig.job(job_id_of(rig))
    post = rig.post(post_id)
    assert job.last_error == post.last_error == "imagem: a geração ficou com desfecho desconhecido"
    [revision] = rig.revisions(post_id)
    with rig.factory() as session:
        audits = [str(a.detail) for a in session.scalars(select(AuditLog))]
    haystack = " ".join(
        [str(job.last_error), str(post.last_error), str(revision.review_notes), *audits]
    )
    assert HOSTILE not in haystack


def test_designer_crash_has_a_fixed_reason(make_rig: MakeRig) -> None:
    rig = make_rig(fake_scenarios.caption_draft() + [RuntimeError(HOSTILE)])  # type: ignore[arg-type,list-item]
    outcome, post_id = generation(rig)
    assert outcome == "drafting_failed"
    job = rig.job(job_id_of(rig))
    assert job.last_error == rig.post(post_id).last_error == "imagem: o designer falhou"


def test_designer_configuration_failure_keeps_its_safe_text(make_rig: MakeRig) -> None:
    only_text: frozenset[Capability] = frozenset({"text"})
    rig = make_rig(package_pass(), designer_capabilities=only_text)
    post_id = rig.seed_post()
    with pytest.raises(RunConfigurationFailed) as caught:
        rig.pipeline.generate(rig.claimed(post_id))
    assert describe_error(caught.value) == (
        "RunConfigurationFailed: designer: MissingCapability: papel 'designer' exige "
        "capacidades em falta: ['json_schema']"
    )
    assert rig.revisions(post_id) == []


# --- ativos e hash ---------------------------------------------------------------------------


def test_revision_hash_includes_final_image_sha() -> None:
    fields = {
        "caption": "legenda",
        "hashtags": ["#a"],
        "call_to_action": "apelo",
        "alt_text": "alt",
        "claims": [{"texto": "t", "kb_ref": None}],
        "channels": ["instagram", "facebook"],
    }
    sha = "ab" * 32
    body = {
        "alegacoes": fields["claims"],
        "alt_text": "alt",
        "apelo": "apelo",
        "canais": ["facebook", "instagram"],
        "hashtags": ["#a"],
        "imagem_sha256": sha,
        "legenda": "legenda",
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert (
        canonical_revision_hash(**fields, final_image_sha256=sha)
        == hashlib.sha256(  # type: ignore[arg-type]
            encoded.encode()
        ).hexdigest()
    )
    # Sem imagem (ou `None`) o hash e o do 2A: a chave nem aparece no JSON.
    without = canonical_revision_hash(**fields)  # type: ignore[arg-type]
    assert without == canonical_revision_hash(**fields, final_image_sha256=None)  # type: ignore[arg-type]
    assert without != canonical_revision_hash(**fields, final_image_sha256=sha)  # type: ignore[arg-type]
    other = canonical_revision_hash(**fields, final_image_sha256="cd" * 32)  # type: ignore[arg-type]
    assert other != canonical_revision_hash(**fields, final_image_sha256=sha)  # type: ignore[arg-type]


def test_identical_recomposition_reuses_final_asset(make_rig: MakeRig) -> None:
    rig = make_rig()
    first, second = rig.seed_post(f"{WEEK}-1"), rig.seed_post(f"{WEEK}-2")
    for post_id in (first, second):
        rig.llm.load(
            [*fake_scenarios.caption_draft(), *photo_visual(rig, titulo="Pao quente"), *review()]
        )
        assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"

    [a], [b] = rig.revisions(first), rig.revisions(second)
    assert a.final_asset_id == b.final_asset_id is not None
    assert len(rig.assets("final")) == 1  # os mesmos bytes (mesmo sha256): um so ativo


def test_stored_background_is_normalized_without_metadata(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass())
    assert generation(rig)[0] == "in_review"
    [background] = rig.assets("componente_gerado")
    data = rig.library.read_asset(rig.brand_id, background.id).data
    assert data.startswith(b"\x89PNG") and hashlib.sha256(data).hexdigest() == background.sha256
    assert (background.created_by, background.is_active) == ("agente", True)


# --- obsoletos e execucoes ligadas ao job ----------------------------------------------------


def test_owner_revision_during_job_still_not_overwritten(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass(), image_price=PRICE)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    rig.llm.hooks[3] = lambda: rig.add_owner_revision(post_id)  # o responsavel edita no Revisor

    assert rig.pipeline.generate(job) == "stale"

    [revision] = rig.revisions(post_id)
    assert (revision.origin, revision.final_asset_id) == ("edicao_responsavel", None)
    assert rig.post(post_id).lifecycle == "drafting"
    assert rig.job(job.id).status == "succeeded"
    # Nada do pacote foi gravado: nem ativos, nem linhas de media novas...
    assert rig.assets("final") == [] and rig.assets("componente_gerado") == []
    with rig.factory() as session:
        kinds = sorted(session.scalars(select(MediaAsset.kind)))
    assert kinds == ["foto", "logotipo"]
    # ...mas o que o fornecedor faturou continua contado.
    [entry] = rig.image_entries()
    assert entry.cost_usd == Decimal(PRICE)


def test_package_runs_are_linked_to_the_job_and_costs_sum_all_of_them(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass(), image_price=PRICE)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    assert rig.pipeline.generate(job) == "in_review"
    runs = {run.role: run for run in rig.runs(job.id)}  # o relogio fixo nao ordena por inicio
    assert {role: run.trigger for role, run in runs.items()} == {
        "copywriter": run_trigger("copy", job.id),
        "designer": run_trigger("design", job.id),
        "revisor": run_trigger("review", job.id),
    }
    [revision] = rig.revisions(post_id)
    assert revision.produced_by_run_id == runs["copywriter"].id
    assert revision.package_cost_usd == Decimal(PRICE)


def test_quota_is_per_request_and_survives_a_dead_worker(make_rig: MakeRig) -> None:
    rig = make_rig(package_pass(), image_price=PRICE)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)

    rig.llm.hooks[3] = kill_worker  # morre depois de a imagem ser faturada, antes do Revisor
    with pytest.raises(SystemExit):
        rig.pipeline.generate(job)
    assert remaining_images(rig.factory, rig.brand_id, job.id) == 1  # 1 faturada

    # O job e reclamado: a 2.a tentativa comeca com 1 lugar (nao com 2).
    rig.expire_leases()
    assert rig.queue.requeue_expired() == 1
    retry = rig.queue.claim("w2")
    assert retry is not None and retry.id == job.id and retry.attempts == 2
    script = package_pass("rever", ["imagem_inadequada"], "Mal.") + review(
        "rever", ("imagem_inadequada",), "Mal."
    )
    rig.llm.load(script)
    assert rig.pipeline.generate(retry) == "in_review"

    # A 2.a tentativa gerou 1 fundo (1 -> 0) e o pedido de fundo novo parou por falta de quota:
    # 2 imagens faturadas no total, nunca 3.
    assert len(rig.image_entries()) == 2 and rig.generated_requests() == 2
    [revision] = rig.revisions(post_id)
    assert revision.review_notes is not None and IMAGE_LIMIT_NOTE in revision.review_notes
    assert (revision.image_cost_usd, revision.package_cost_usd) == (Decimal("0.08"),) * 2
    assert remaining_images(rig.factory, rig.brand_id, job.id) == 0

    # Um pedido NOVO (alteracoes do responsavel) e outro job: quota cheia outra vez.
    assert remaining_images(rig.factory, rig.brand_id, "outro-job") == 2


def test_quota_counts_open_reservations_and_ignores_released_ones(make_rig: MakeRig) -> None:
    generator = ScriptedImageGenerator([ImageProviderError("x", 500)])
    rig = make_rig(package_pass(), generator=generator)
    _, post_id = generation(rig)
    job_id = job_id_of(rig)
    assert remaining_images(rig.factory, rig.brand_id, job_id) == 1  # unknown_outcome conta
    with rig.factory.begin() as session:
        for reservation in session.scalars(select(CostReservation)):
            if reservation.reserved_usd > 0 or reservation.status == "unknown_outcome":
                reservation.status = "released"
    assert remaining_images(rig.factory, rig.brand_id, job_id) == 2  # libertada: nada faturado
    with rig.factory.begin() as session:
        for reservation in session.scalars(select(CostReservation)):
            reservation.status = "open" if reservation.status == "released" else reservation.status
    assert remaining_images(rig.factory, rig.brand_id, job_id) == 1
    assert post_id


def test_photo_bytes_error_has_a_fixed_message_without_paths(make_rig: MakeRig) -> None:
    rig = make_rig()
    media = DesignMedia(rig.library, rig.kits)
    inputs = media.load(rig.brand_id)
    assert inputs.photo_bytes(rig.photo_id)[:3] == b"\xff\xd8\xff"
    rig.library.deactivate(rig.brand_id, rig.photo_id, "ana")
    with pytest.raises(Exception) as caught:
        inputs.photo_bytes(rig.photo_id)
    error = caught.value
    assert str(error) == PHOTO_UNAVAILABLE_MESSAGE
    assert error.__cause__ is None and error.__context__ is None
    assert rig.photo_id not in repr(error)
    # Uma foto que nunca existiu (ou de outra marca) tem a mesma mensagem fixa.
    with pytest.raises(Exception) as unknown:
        inputs.photo_bytes("nao-existe")
    assert str(unknown.value) == PHOTO_UNAVAILABLE_MESSAGE


def test_inactive_photo_is_not_offered_to_the_designer(make_rig: MakeRig) -> None:
    rig = make_rig()
    rig.library.deactivate(rig.brand_id, rig.photo_id, "ana")
    assert DesignMedia(rig.library, rig.kits).load(rig.brand_id).photos == {}


def test_post_row_is_untouched_by_a_successful_package_beyond_the_lifecycle(
    make_rig: MakeRig,
) -> None:
    rig = make_rig(package_pass())
    post_id = rig.seed_post()
    assert rig.pipeline.generate(rig.claimed(post_id)) == "in_review"
    with rig.factory() as session:
        post = session.get_one(Post, post_id)
    assert (post.lifecycle, post.last_error, post.review_rounds) == ("in_review", None, 0)


# --- ordem dos locks -------------------------------------------------------------------------


def test_plan_reapproval_during_image_failure_save_does_not_deadlock(make_rig: MakeRig) -> None:
    """A gravacao da falha de imagem tambem bloqueia o post antes do job (como a aprovacao)."""
    rig = make_rig(fake_scenarios.caption_draft(), with_kit=False)
    post_id = rig.seed_post()
    job = rig.claimed(post_id)
    reached, approval_holds_post = threading.Event(), threading.Event()

    def pause_before_save() -> None:
        reached.set()
        assert approval_holds_post.wait(10)

    rig.llm.hooks[1] = pause_before_save
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.pipeline.generate, job)
        assert reached.wait(10)
        with rig.factory.begin() as session:  # a transacao de aprovacao do plano
            session.scalars(select(Post).where(Post.id == post_id).with_for_update()).one()
            approval_holds_post.set()
            wait_for_lock_waiter(rig.factory)  # o pipeline espera pelo post, sem tocar no job
            rig.queue.cancel_for_post_in(session, post_id)  # segundo lock: o job
            session.execute(update(Post).where(Post.id == post_id).values(lifecycle="planned"))
        assert future.result(timeout=20) == "stale"  # um impasse apareceria aqui

    assert rig.revisions(post_id) == [] and rig.job(job.id).status == "cancelled"
