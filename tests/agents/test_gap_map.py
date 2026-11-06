"""Mapa de lacunas: estado inicial, fusao de atualizacoes e correcao do responsavel."""

import pytest

from marketing_agent.agents.diretor.gap_map import (
    GAP_TOPICS,
    GapItem,
    confirm_by_owner,
    initial_gap_map,
    merge_gap_map,
    parse_gap_map,
    serialize_gap_map,
)


def item_for(items: list[GapItem], topic: str) -> GapItem:
    return next(item for item in items if item.topic == topic)


def test_initial_gap_map_all_unknown() -> None:
    items = initial_gap_map()
    assert [item.topic for item in items] == list(GAP_TOPICS)
    assert len(GAP_TOPICS) == 14
    assert all(i.status == "desconhecido" and i.note == "" and i.origin is None for i in items)


def test_merge_ignores_unknown_topics() -> None:
    update = GapItem(topic="inexistente", status="confirmado", note="x", origin="verificado")
    merged = merge_gap_map(initial_gap_map(), [update])
    assert merged == initial_gap_map()


def test_merge_replaces_item_that_is_not_owner_confirmed() -> None:
    update = GapItem(topic="geografia", status="suposicao", note="Porto", origin="suposto")
    merged = merge_gap_map(initial_gap_map(), [update])
    assert item_for(merged, "geografia") == update
    assert [i.topic for i in merged] == list(GAP_TOPICS)


def test_merge_keeps_owner_confirmed() -> None:
    owner = GapItem(
        topic="geografia", status="confirmado", note="Porto", origin="dito_pelo_responsavel"
    )
    current = merge_gap_map(initial_gap_map(), [owner])
    downgrade = GapItem(topic="geografia", status="suposicao", note="Lisboa", origin="suposto")
    assert item_for(merge_gap_map(current, [downgrade]), "geografia") == owner


def test_merge_can_replace_confirmed_item_not_from_owner() -> None:
    verified = GapItem(topic="geografia", status="confirmado", note="a", origin="verificado")
    current = merge_gap_map(initial_gap_map(), [verified])
    update = GapItem(topic="geografia", status="confirmado", note="b", origin="verificado")
    assert item_for(merge_gap_map(current, [update]), "geografia").note == "b"


def test_confirm_by_owner_overrides_and_rejects_unknown_topic() -> None:
    suposto = GapItem(topic="voz_e_tom", status="suposicao", note="x", origin="suposto")
    current = merge_gap_map(initial_gap_map(), [suposto])
    updated = confirm_by_owner(current, "voz_e_tom", "Calma e direta")
    assert item_for(updated, "voz_e_tom") == GapItem(
        topic="voz_e_tom",
        status="confirmado",
        note="Calma e direta",
        origin="dito_pelo_responsavel",
    )
    with pytest.raises(ValueError, match="inexistente"):
        confirm_by_owner(current, "inexistente", "x")


def test_serialize_roundtrip() -> None:
    update = GapItem(topic="objetivos", status="suposicao", note="n", origin="suposto")
    items = merge_gap_map(initial_gap_map(), [update])
    assert parse_gap_map(serialize_gap_map(items)) == items
