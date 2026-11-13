"""Atribuicao deterministica e DTO sem proveniencia dos percursos."""

from dataclasses import fields

from marketing_agent.evals.blind import BlindSide, side_a_path


def test_side_assignment_reproducible_from_seed():
    assert [side_a_path(42, key) for key in ("a", "b", "a")] == [
        side_a_path(42, "a"),
        side_a_path(42, "b"),
        side_a_path(42, "a"),
    ]


def test_side_assignment_balanced():
    specialized = sum(side_a_path(seed, "brief") == "especializado" for seed in range(1000))
    assert 450 <= specialized <= 550


def test_blind_side_has_no_identifying_fields():
    assert {f.name for f in fields(BlindSide)} == {
        "legenda",
        "hashtags",
        "apelo_a_acao",
        "alt_text",
        "alegacoes",
        "is_ai_generated",
    }
