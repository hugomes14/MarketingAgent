"""Os posts da semana bloqueiam-se por ordem de id (o mesmo criterio de `requeue_expired`)."""

import re

from content_rig import WEEK, Factory, build_rig
from sqlalchemy import event
from sqlalchemy.engine import Connection

from marketing_agent.clock import FixedClock
from marketing_agent.planning.posts import PostReconciler
from marketing_agent.planning.schemas import PlanoSemanal


def test_week_posts_are_locked_in_id_order(
    session_factory: Factory, clock: FixedClock, brand_id: str
) -> None:
    rig = build_rig(session_factory, clock, brand_id, [])
    rig.seed_post(f"{WEEK}-2")
    rig.seed_post(f"{WEEK}-1")
    statements: list[str] = []

    def capture(conn: Connection, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    reconciler = PostReconciler(rig.queue, clock)
    plan_record = rig.store.get(rig.plan_id)
    plan = PlanoSemanal.model_construct(semana=WEEK, slots=[])
    with rig.factory.begin() as session:
        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", capture)
        try:
            reconciler.apply(session, plan_record, plan, rig.strategy_id)
        finally:
            event.remove(bind, "before_cursor_execute", capture)

    [locking] = [s for s in statements if "FROM post" in s and "FOR UPDATE" in s]
    assert re.search(r"ORDER BY post\.id\s+FOR UPDATE", locking)
