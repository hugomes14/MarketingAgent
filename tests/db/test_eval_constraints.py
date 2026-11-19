"""Contratos das tabelas da avaliacao cega (migracao 0004)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.db import models

AGORA = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
Fabrica = sessionmaker[Session]


def gravar_estrategia(fabrica: Fabrica, marca_id: str) -> str:
    with fabrica.begin() as sessao:
        artefacto = models.Artifact(
            brand_id=marca_id, type="estrategia", schema_version=1, state="approved", payload={}
        )
        sessao.add(artefacto)
        sessao.flush()
        return artefacto.id


def nova_corrida(marca_id: str, estrategia_id: str, status: str = "running") -> models.EvalRun:
    return models.EvalRun(
        brand_id=marca_id,
        set_name="briefs_v1",
        set_version=1,
        seed=2**62,
        status=status,
        strategy_artifact_id=estrategia_id,
        briefs=[],
        context_snapshot={},
        estimate_usd=Decimal("24"),
        created_by="teste",
        created_at=AGORA,
    )


def gravar_corrida(fabrica: Fabrica, marca_id: str, status: str = "running") -> str:
    estrategia_id = gravar_estrategia(fabrica, marca_id)
    with fabrica.begin() as sessao:
        corrida = nova_corrida(marca_id, estrategia_id, status)
        sessao.add(corrida)
        sessao.flush()
        return corrida.id


def novo_item(marca_id: str, corrida_id: str, path: str = "simples") -> models.EvalItem:
    return models.EvalItem(
        brand_id=marca_id,
        run_id=corrida_id,
        brief_key="pao",
        path=path,
        status="queued",
        created_at=AGORA,
    )


def nova_resposta(
    marca_id: str, corrida_id: str, supersedes_id: str | None = None, **campos: object
) -> models.EvalJudgement:
    valores: dict[str, object] = {
        "brand_id": marca_id,
        "run_id": corrida_id,
        "brief_key": "pao",
        "side_a_path": "simples",
        "better": "a",
        "errors_a": [],
        "errors_b": ["voz_errada"],
        "edit_a": "0",
        "edit_b": "2_5",
        "seconds_spent": 40,
        "decided_by": "teste",
        "decided_at": AGORA,
        "supersedes_id": supersedes_id,
    }
    valores.update(campos)
    return models.EvalJudgement(**valores)


def gravar_resposta(fabrica: Fabrica, marca_id: str, corrida_id: str, **campos: object) -> str:
    with fabrica.begin() as sessao:
        resposta = nova_resposta(marca_id, corrida_id, **campos)  # type: ignore[arg-type]
        sessao.add(resposta)
        sessao.flush()
        return resposta.id


def test_one_running_eval_run_per_brand(session_factory: Fabrica, brand_id: str) -> None:
    gravar_corrida(session_factory, brand_id)
    gravar_corrida(session_factory, brand_id, status="ready")  # so a `running` e unica
    with pytest.raises(IntegrityError):
        gravar_corrida(session_factory, brand_id)


def test_eval_run_rejects_bad_set_name(session_factory: Fabrica, brand_id: str) -> None:
    estrategia_id = gravar_estrategia(session_factory, brand_id)
    corrida = nova_corrida(brand_id, estrategia_id)
    corrida.set_name = "../x"
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(corrida)


def test_eval_item_unique_per_brief_and_path(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        sessao.add(novo_item(brand_id, corrida_id, "simples"))
        sessao.add(novo_item(brand_id, corrida_id, "especializado"))
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(novo_item(brand_id, corrida_id, "simples"))


def test_eval_item_rejects_unknown_path(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(novo_item(brand_id, corrida_id, "outro"))


def test_cross_brand_eval_item_rejected(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with session_factory.begin() as sessao:
        outra = models.Brand(name="Outra")
        sessao.add(outra)
        sessao.flush()
        outra_id = outra.id
    with pytest.raises(IntegrityError), session_factory.begin() as sessao:
        sessao.add(novo_item(outra_id, corrida_id))


@pytest.mark.parametrize(
    "campos",
    [{"errors_a": ["inventado"]}, {"edit_b": "3"}, {"better": "c"}, {"seconds_spent": 3601}],
)
def test_judgement_rejects_unknown_error_kind_and_edit_value(
    session_factory: Fabrica, brand_id: str, campos: dict[str, object]
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    with pytest.raises(IntegrityError):
        gravar_resposta(session_factory, brand_id, corrida_id, **campos)


def test_judgement_update_and_delete_blocked_by_trigger(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    resposta_id = gravar_resposta(session_factory, brand_id, corrida_id)
    with pytest.raises(DBAPIError, match="imutaveis"), session_factory.begin() as sessao:
        sessao.execute(
            update(models.EvalJudgement)
            .where(models.EvalJudgement.id == resposta_id)
            .values(better="b")
        )
    with pytest.raises(DBAPIError, match="imutaveis"), session_factory.begin() as sessao:
        sessao.execute(text("DELETE FROM eval_judgement WHERE id = :id"), {"id": resposta_id})


def test_judgement_supersedes_once_and_first_answer_unique(
    session_factory: Fabrica, brand_id: str
) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id)
    primeira = gravar_resposta(session_factory, brand_id, corrida_id)
    with pytest.raises(IntegrityError):
        gravar_resposta(session_factory, brand_id, corrida_id)  # segunda "primeira" resposta
    segunda = gravar_resposta(session_factory, brand_id, corrida_id, supersedes_id=primeira)
    with pytest.raises(IntegrityError):
        gravar_resposta(session_factory, brand_id, corrida_id, supersedes_id=primeira)
    gravar_resposta(session_factory, brand_id, corrida_id, supersedes_id=segunda)


def test_role_decision_is_append_only(session_factory: Fabrica, brand_id: str) -> None:
    corrida_id = gravar_corrida(session_factory, brand_id, status="revealed")
    with session_factory.begin() as sessao:
        decisao = models.RoleDecision(
            brand_id=brand_id,
            run_id=corrida_id,
            role="revisor",
            decision="manter",
            justificacao="Encontra erros que eu nao vi.",
            decided_by="teste",
            decided_at=AGORA,
        )
        sessao.add(decisao)
        sessao.flush()
        decisao_id = decisao.id
    with pytest.raises(DBAPIError, match="imutaveis"), session_factory.begin() as sessao:
        sessao.execute(text("DELETE FROM role_decision WHERE id = :id"), {"id": decisao_id})


def test_job_type_accepts_eval_package(session_factory: Fabrica) -> None:
    with session_factory.begin() as sessao:
        sessao.add(
            models.Job(
                type="eval_package",
                payload={"eval_run_id": "r", "eval_item_id": "i"},
                status="queued",
                run_at=AGORA,
                created_at=AGORA,
                updated_at=AGORA,
            )
        )
