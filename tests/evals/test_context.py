"""Leituras por id que fixam o contexto de uma corrida (Plano 2C, T5): base e feedback."""

from datetime import date

from content_rig import Factory
from eval_rig import MakeEvalRig

from marketing_agent.db.models import Brand


def other_brand(factory: Factory) -> str:
    with factory.begin() as session:
        brand = Brand(name="Outra marca")
        session.add(brand)
        session.flush()
        return brand.id


def test_kb_entries_by_ids_keep_the_order_and_ignore_activity_and_validity(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig()
    first, second = rig.add_price_item("a"), rig.add_price_item("b", valid_until=date(2026, 11, 6))
    rig.content.kb.deactivate(rig.brand_id, first, "ana")
    found = rig.content.kb.entries_by_ids(rig.brand_id, [second, first, "inexistente"])
    assert list(found) == [second, first]
    assert found[first].is_active is False


def test_kb_entries_by_ids_never_cross_brands(make_eval_rig: MakeEvalRig) -> None:
    rig = make_eval_rig()
    item = rig.add_price_item("a")
    assert rig.content.kb.entries_by_ids(other_brand(rig.factory), [item]) == {}
    assert rig.content.kb.entries_by_ids(rig.brand_id, []) == {}


def test_feedback_examples_by_ids_keep_the_order_and_ignore_deactivation(
    make_eval_rig: MakeEvalRig,
) -> None:
    rig = make_eval_rig()
    first, second = rig.add_style_example(), rig.add_style_example()
    rig.content.feedback.deactivate(rig.brand_id, first, "ana")
    views = rig.content.feedback.examples_by_ids(rig.brand_id, [second, first, "inexistente"])
    assert [view.id for view in views] == [second, first]
    assert rig.content.feedback.examples_by_ids(other_brand(rig.factory), [first]) == []
