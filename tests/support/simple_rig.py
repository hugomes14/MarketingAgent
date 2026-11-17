"""Auxiliares dos testes do percurso simples (Plano 2C): pedido, caminho e gasto semeado."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from content_rig import PROFILE_PAYLOAD, ContentRig, Factory
from sqlalchemy import func, select

from marketing_agent.content.loops import CopyPayload
from marketing_agent.content.package import PackageContext, PackageRequest
from marketing_agent.content.package_costs import PackageTriggers
from marketing_agent.content.package_media import DesignMedia, DesignSnapshot
from marketing_agent.content.specs import load_content_specs
from marketing_agent.db.models import AgentRun, CostEntry, CostReservation
from marketing_agent.evals.simple_path import SimplePath

EVAL_TRIGGER = "eval_item:i1"


def copy_payload(**changes: object) -> CopyPayload:
    """Contexto de texto minimo (Instagram, sem itens da base)."""
    payload = CopyPayload(
        brief={"tema": "Pao do dia", "pilar": "Producao artesanal", "objetivo": "Mensagens",
               "kb_refs": []},
        channels=["instagram"],
        perfil_marca=PROFILE_PAYLOAD,
        pilares=["Producao artesanal"],
        accepted_proposals=[],
        kb_entries={},
        style_examples=[],
        negative_examples=[],
        reviewer_notes=None,
        owner_comment=None,
    )  # fmt: skip
    return replace(payload, **changes)


def request_for(
    rig: ContentRig,
    trigger: str = EVAL_TRIGGER,
    *,
    payload: CopyPayload | None = None,
    owner_comment: str | None = None,
    snapshot: DesignSnapshot | None = None,
) -> PackageRequest:
    """Pedido de avaliacao (familia de um so trigger `eval_item:<id>`)."""
    context = PackageContext(payload or copy_payload(), owner_comment, snapshot)
    return PackageRequest(rig.brand_id, PackageTriggers.single(trigger), context)


def simple_path(rig: ContentRig, ceiling: str = "1.50") -> SimplePath:
    """Percurso simples sobre o motor e a media do rig, com o spec carregado de verdade."""
    return SimplePath(
        rig.factory,
        rig.engine,
        load_content_specs().simple,
        DesignMedia(rig.library, rig.kits),
        "medium",
        Decimal(ceiling),
    )


def seed_family_spend(
    factory: Factory,
    brand_id: str,
    trigger: str,
    *,
    now: datetime,
    spent: str = "0",
    images: int = 0,
) -> None:
    """Execucao do `trigger` com gasto acertado (`spent`) e `images` lancamentos de imagem."""
    with factory.begin() as session:
        run = AgentRun(
            brand_id=brand_id, role="simples", spec_version=1, trigger=trigger,
            status="succeeded", cost_usd=Decimal(spent), started_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        amounts = [(Decimal(spent), {})] if Decimal(spent) > 0 else []
        amounts += [(Decimal("0.04"), {"images": 1})] * images
        for amount, units in amounts:
            reservation = CostReservation(
                brand_id=brand_id, run_id=run.id, role="simples", provider="fake",
                model_id="fake-1", price_version="v1", reserved_usd=amount, status="settled",
                created_at=now,
            )  # fmt: skip
            session.add(reservation)
            session.flush()
            session.add(
                CostEntry(
                    brand_id=brand_id,
                    reservation_id=reservation.id,
                    role="simples",
                    run_id=run.id,
                    provider="fake",
                    model_id="fake-1",
                    price_version="v1",
                    tokens_in=0,
                    tokens_out=0,
                    units=units,
                    cost_usd=amount,
                    kind="measured",
                    at=now,
                )  # fmt: skip
            )


def count_rows(factory: Factory, model: type) -> int:
    """Numero de linhas da tabela do modelo."""
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0
