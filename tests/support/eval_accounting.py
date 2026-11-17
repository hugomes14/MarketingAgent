"""Reservas sinteticas para simular recuperacao e acerto tardio sem fornecedor externo."""

from decimal import Decimal


def add_exposure(rig, item, amount, status):
    from marketing_agent.db.models import AgentRun, CostReservation
    from marketing_agent.images.generation import FAKE_IMAGE_MODEL

    with rig.factory.begin() as session:
        run = AgentRun(
            brand_id=rig.brand_id,
            role="designer",
            spec_version=1,
            trigger=f"eval_item:{item.id}",
            status="failed",
            started_at=rig.content.clock.now(),
            ended_at=rig.content.clock.now(),
        )
        session.add(run)
        session.flush()
        row = CostReservation(
            brand_id=rig.brand_id,
            run_id=run.id,
            role="designer",
            provider="fake",
            model_id=FAKE_IMAGE_MODEL,
            price_version="v1",
            reserved_usd=Decimal(amount),
            status=status,
            created_at=rig.content.clock.now(),
        )
        session.add(row)
        session.flush()
        return row.id
