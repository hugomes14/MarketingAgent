"""Contratos da comparacao cega: corridas, pacotes, respostas imutaveis e decisoes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.clock import FixedClock
from marketing_agent.db import models
from marketing_agent.jobs.queue import JobQueue

AGORA = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
Fabrica = sessionmaker[Session]
LEASE = timedelta(minutes=10)


def gravar_estrategia(fabrica: Fabrica, marca_id: str) -> str:
    """Grava o artefacto de estrategia que enquadra uma corrida."""
    with fabrica.begin() as sessao:
        artefacto = models.Artifact(
            brand_id=marca_id, type="estrategia", schema_version=1, state="approved", payload={}
        )
        sessao.add(artefacto)
        sessao.flush()
        return artefacto.id


def criar_corrida(marca_id: str, estrategia_id: str, estado: str = "running") -> models.EvalRun:
    """Prepara uma corrida valida; `revealed_at` acompanha o estado `revealed`."""
    return models.EvalRun(
        brand_id=marca_id,
        set_name="briefs_v1",
        set_version=1,
        seed=2**62,
        status=estado,
        strategy_artifact_id=estrategia_id,
        briefs=[{"key": "b1"}],
        context_snapshot={"kb_item_ids": []},
        estimate_usd=Decimal("3.000000"),
        created_by="teste",
        created_at=AGORA,
        revealed_at=AGORA if estado == "revealed" else None,
    )


def gravar_corrida(fabrica: Fabrica, marca_id: str, estado: str = "running") -> str:
    """Grava uma corrida (com a sua estrategia) e devolve o id."""
    estrategia_id = gravar_estrategia(fabrica, marca_id)
    with fabrica.begin() as sessao:
        corrida = criar_corrida(marca_id, estrategia_id, estado)
        sessao.add(corrida)
        sessao.flush()
        return corrida.id


def criar_item(
    marca_id: str, corrida_id: str, chave: str = "b1", percurso: str = "simples"
) -> models.EvalItem:
    """Prepara um pacote pendente."""
    return models.EvalItem(
        brand_id=marca_id,
        run_id=corrida_id,
        brief_key=chave,
        path=percurso,
        status="queued",
    )


def criar_resposta(marca_id: str, corrida_id: str, chave: str = "b1") -> models.EvalJudgement:
    """Prepara a primeira resposta valida de um par."""
    return models.EvalJudgement(
        brand_id=marca_id,
        run_id=corrida_id,
        brief_key=chave,
        side_a_path="simples",
        better="a",
        errors_a=["voz_errada"],
        errors_b=[],
        edit_a="1",
        edit_b="2_5",
        note="Nota",
        seconds_spent=42,
        decided_by="responsavel",
        decided_at=AGORA,
    )


def gravar_resposta(fabrica: Fabrica, marca_id: str, corrida_id: str, chave: str = "b1") -> str:
    """Grava a primeira resposta de um par."""
    with fabrica.begin() as sessao:
        resposta = criar_resposta(marca_id, corrida_id, chave)
        sessao.add(resposta)
        sessao.flush()
        return resposta.id


def criar_decisao(marca_id: str, corrida_id: str) -> models.RoleDecision:
    """Prepara uma decisao valida sobre um papel."""
    return models.RoleDecision(
        brand_id=marca_id,
        run_id=corrida_id,
        role="revisor",
        decision="manter",
        justificacao="O revisor apanhou erros factuais.",
        decided_by="responsavel",
        decided_at=AGORA,
    )


def nova_marca(fabrica: Fabrica) -> str:
    """Cria uma segunda marca."""
    with fabrica.begin() as sessao:
        marca = models.Brand(name="Outra marca")
        sessao.add(marca)
        sessao.flush()
        return marca.id


def test_constants_match_plan() -> None:
    assert models.EVAL_RUN_STATUSES == ("running", "ready", "revealed")
    assert models.EVAL_PATHS == ("simples", "especializado")
    assert models.EVAL_ITEM_STATUSES == ("queued", "running", "succeeded", "failed")
    assert models.EVAL_BETTER == ("a", "b", "empate")
    assert models.EVAL_ERROR_KINDS == (
        "facto_errado",
        "alegacao_sem_apoio",
        "voz_errada",
        "imagem_inadequada",
        "outro",
    )
    assert models.EDIT_MINUTES == ("0", "1", "2_5", "mais_5")
    assert models.DECIDABLE_ROLES == ("copywriter", "designer", "revisor")
    assert models.ROLE_DECISIONS == ("manter", "simplificar", "retirar")


def test_one_running_eval_run_per_brand(session_factory: Fabrica, brand_id: str) -> None:
    gravar_corrida(session_factory, brand_id)
    estrategia_id = gravar_estrategia(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_corrida(brand_id, estrategia_id))


def test_running_run_allowed_for_another_brand(session_factory: Fabrica, brand_id: str) -> None:
    gravar_corrida(session_factory, brand_id)
    outra_id = nova_marca(session_factory)
    gravar_corrida(session_factory, outra_id)


@pytest.mark.parametrize("estado", ["ready", "revealed"])
def test_new_running_run_allowed_after_finished_one(
    session_factory: Fabrica, brand_id: str, estado: str
) -> None:
    gravar_corrida(session_factory, brand_id, estado)
    gravar_corrida(session_factory, brand_id)
    gravar_corrida(session_factory, brand_id, "ready")


@pytest.mark.parametrize(
    "atribuicao",
    [
        {"status": "done"},
        {"set_version": 0},
        {"set_name": "../x"},
        {"set_name": "A"},
        {"estimate_usd": Decimal("-1")},
        {"status": "revealed", "revealed_at": None},
        {"status": "ready", "revealed_at": AGORA},
    ],
)
def test_eval_run_rejects_invalid_values(
    session_factory: Fabrica, brand_id: str, atribuicao: dict[str, object]
) -> None:
    estrategia_id = gravar_estrategia(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        corrida = criar_corrida(brand_id, estrategia_id, "ready")
        for campo, valor in atribuicao.items():
            setattr(corrida, campo, valor)
        sessao.add(corrida)


def test_eval_run_seed_holds_63_bits(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory() as sessao:
        assert sessao.get(models.EvalRun, corrida_id).seed == 2**62  # type: ignore[union-attr]


def test_eval_run_strategy_must_belong_to_same_brand(
    session_factory: Fabrica, brand_id: str
) -> None:
    outra_id = nova_marca(session_factory)
    estrategia_alheia = gravar_estrategia(session_factory, outra_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_corrida(brand_id, estrategia_alheia))


def test_eval_item_unique_per_brief_and_path(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        sessao.add(criar_item(brand_id, corrida_id, "b1", "simples"))
        sessao.add(criar_item(brand_id, corrida_id, "b1", "especializado"))
        sessao.add(criar_item(brand_id, corrida_id, "b2", "simples"))
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_item(brand_id, corrida_id, "b1", "simples"))


def test_eval_item_defaults_are_neutral(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        item = criar_item(brand_id, corrida_id)
        sessao.add(item)
        sessao.flush()
        item_id = item.id
    with session_factory() as sessao:
        item = sessao.get(models.EvalItem, item_id)
        assert item is not None
        assert (item.cost_usd, item.invalid_outputs, item.review_rounds) == (Decimal(0), 0, 0)
        assert (item.latency_ms, item.revision_snapshot, item.final_asset_id) == (None, None, None)
        assert (item.job_id, item.last_error) == (None, None)


@pytest.mark.parametrize(
    "atribuicao",
    [
        {"path": "outro"},
        {"status": "done"},
        {"brief_key": "Brief 1"},
        {"brief_key": ""},
        {"cost_usd": Decimal("-0.01")},
        {"latency_ms": -1},
        {"invalid_outputs": -1},
        {"review_rounds": -1},
        {"status": "succeeded"},
    ],
)
def test_eval_item_rejects_invalid_values(
    session_factory: Fabrica, brand_id: str, atribuicao: dict[str, object]
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        item = criar_item(brand_id, corrida_id)
        for campo, valor in atribuicao.items():
            setattr(item, campo, valor)
        sessao.add(item)


def test_eval_item_succeeded_with_snapshot_accepted(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        item = criar_item(brand_id, corrida_id)
        item.status = "succeeded"
        item.revision_snapshot = {"caption": "Texto"}
        sessao.add(item)


def test_cross_brand_eval_item_rejected(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    outra_id = nova_marca(session_factory)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_item(outra_id, corrida_id))


def test_cross_brand_eval_item_asset_rejected(session_factory: Fabrica, brand_id: str) -> None:
    outra_id = nova_marca(session_factory)
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        media = models.MediaAsset(
            brand_id=outra_id,
            kind="final",
            storage_backend="local",
            storage_key="x.png",
            mime="image/png",
            width=10,
            height=10,
            bytes=1,
            sha256="c" * 64,
            origem="composta",
            created_by="teste",
            created_at=AGORA,
        )
        sessao.add(media)
        sessao.flush()
        media_id = media.id
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        item = criar_item(brand_id, corrida_id)
        item.final_asset_id = media_id
        sessao.add(item)


def test_cross_brand_judgement_and_decision_rejected(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    outra_id = nova_marca(session_factory)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_resposta(outra_id, corrida_id))
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_decisao(outra_id, corrida_id))


@pytest.mark.parametrize(
    "atribuicao",
    [
        {"errors_a": ["facto_inventado"]},
        {"errors_b": ["voz_errada", "desconhecido"]},
        {"errors_a": ["outro"] * 6},
        {"edit_a": "3"},
        {"edit_b": "mais_10"},
        {"better": "c"},
        {"side_a_path": "ambos"},
        {"seconds_spent": -1},
        {"seconds_spent": 3601},
        {"note": "x" * 2001},
        {"brief_key": "../b"},
        {"errors_a": None},
        {"supersedes_id": "inexistente"},
    ],
)
def test_judgement_rejects_unknown_error_kind_and_edit_value(
    session_factory: Fabrica, brand_id: str, atribuicao: dict[str, object]
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        resposta = criar_resposta(brand_id, corrida_id)
        for campo, valor in atribuicao.items():
            setattr(resposta, campo, valor)
        sessao.add(resposta)


def test_judgement_accepts_every_known_value(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        for indice, edicao in enumerate(models.EDIT_MINUTES):
            resposta = criar_resposta(brand_id, corrida_id, f"b{indice}")
            resposta.errors_a = list(models.EVAL_ERROR_KINDS)
            resposta.edit_a = edicao
            resposta.seconds_spent = 3600 if indice else 0
            resposta.note = None
            sessao.add(resposta)


@pytest.mark.parametrize(
    "comando",
    [
        "UPDATE eval_judgement SET note = 'alterada' WHERE id = :id",
        "UPDATE eval_judgement SET better = 'b' WHERE id = :id",
        "DELETE FROM eval_judgement WHERE id = :id",
    ],
)
def test_judgement_update_and_delete_blocked_by_trigger(
    session_factory: Fabrica, brand_id: str, comando: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    resposta_id = gravar_resposta(session_factory, brand_id, corrida_id)
    with pytest.raises(IntegrityError, match="imutaveis"), session_factory.begin() as sessao:
        sessao.execute(text(comando), {"id": resposta_id})
    with session_factory() as sessao:
        resposta = sessao.get(models.EvalJudgement, resposta_id)
        assert resposta is not None
        assert (resposta.note, resposta.better) == ("Nota", "a")


def test_judgement_supersedes_once_and_first_answer_unique(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    primeira_id = gravar_resposta(session_factory, brand_id, corrida_id)
    # Uma segunda "primeira" resposta do mesmo par e recusada.
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(criar_resposta(brand_id, corrida_id))
    with session_factory.begin() as sessao:
        correcao = criar_resposta(brand_id, corrida_id)
        correcao.supersedes_id = primeira_id
        correcao.better = "b"
        sessao.add(correcao)
        sessao.flush()
        correcao_id = correcao.id
    # A mesma resposta nao pode ser substituida duas vezes ...
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        outra = criar_resposta(brand_id, corrida_id)
        outra.supersedes_id = primeira_id
        sessao.add(outra)
    # ... mas a correcao pode ser corrigida, e as tres linhas ficam gravadas.
    with session_factory.begin() as sessao:
        terceira = criar_resposta(brand_id, corrida_id)
        terceira.supersedes_id = correcao_id
        sessao.add(terceira)
    with session_factory() as sessao:
        assert len(sessao.scalars(select(models.EvalJudgement)).all()) == 3


def test_judgement_cannot_supersede_itself(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        resposta = criar_resposta(brand_id, corrida_id)
        resposta.id = "propria"
        resposta.supersedes_id = "propria"
        sessao.add(resposta)


def test_judgement_supersedes_other_brief_or_run_rejected(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id, "ready")
    outra_corrida_id = gravar_corrida(session_factory, brand_id)
    primeira_id = gravar_resposta(session_factory, brand_id, corrida_id, "b1")
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        outra = criar_resposta(brand_id, corrida_id, "b2")
        outra.supersedes_id = primeira_id
        sessao.add(outra)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        outra = criar_resposta(brand_id, outra_corrida_id, "b1")
        outra.supersedes_id = primeira_id
        sessao.add(outra)


def test_same_brief_key_allowed_in_different_runs(session_factory: Fabrica, brand_id: str) -> None:
    primeira = gravar_corrida(session_factory, brand_id, "ready")
    segunda = gravar_corrida(session_factory, brand_id)
    gravar_resposta(session_factory, brand_id, primeira)
    gravar_resposta(session_factory, brand_id, segunda)


@pytest.mark.parametrize(
    "comando",
    [
        "UPDATE role_decision SET decision = 'retirar' WHERE id = :id",
        "DELETE FROM role_decision WHERE id = :id",
    ],
)
def test_role_decision_is_append_only(
    session_factory: Fabrica, brand_id: str, comando: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        decisao = criar_decisao(brand_id, corrida_id)
        sessao.add(decisao)
        sessao.flush()
        decisao_id = decisao.id
    with pytest.raises(IntegrityError, match="so se acrescentam"), session_factory.begin() as s:
        s.execute(text(comando), {"id": decisao_id})
    with session_factory.begin() as sessao:
        sessao.add(criar_decisao(brand_id, corrida_id))


@pytest.mark.parametrize(
    "atribuicao",
    [
        {"role": "diretor"},
        {"decision": "apagar"},
        {"justificacao": ""},
        {"justificacao": "x" * 2001},
    ],
)
def test_role_decision_rejects_invalid_values(
    session_factory: Fabrica, brand_id: str, atribuicao: dict[str, object]
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        decisao = criar_decisao(brand_id, corrida_id)
        for campo, valor in atribuicao.items():
            setattr(decisao, campo, valor)
        sessao.add(decisao)


def test_job_type_accepts_eval_package(
    session_factory: Fabrica, clock: FixedClock, brand_id: str
) -> None:
    fila = JobQueue(session_factory, clock, lease=LEASE)
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        item = criar_item(brand_id, corrida_id)
        sessao.add(item)
        sessao.flush()
        payload = {"eval_run_id": corrida_id, "eval_item_id": item.id}
        chave = f"eval_package:{item.id}"
        job_id = fila.enqueue_in(sessao, "eval_package", payload, chave)
        assert job_id is not None
        item.job_id = job_id
        # A mesma chave enquanto o job esta ativo nao duplica.
        assert fila.enqueue_in(sessao, "eval_package", payload, chave) is None
        assert not fila.has_active_for_post_in(sessao, "qualquer")
        assert fila.cancel_for_post_in(sessao, "qualquer") == 0
    job = fila.claim("worker-teste")
    assert job is not None
    assert (job.type, job.payload["eval_run_id"]) == ("eval_package", corrida_id)


def test_tables_are_not_cascaded(session_factory: Fabrica, brand_id: str) -> None:
    """Apagar uma corrida com respostas e recusado: as respostas nunca desaparecem."""
    corrida_id = gravar_corrida(session_factory, brand_id)
    gravar_resposta(session_factory, brand_id, corrida_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.execute(text("DELETE FROM eval_run WHERE id = :id"), {"id": corrida_id})
