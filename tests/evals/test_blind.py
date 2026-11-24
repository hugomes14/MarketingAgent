"""Atribuicao cega: reproduzivel pela semente, equilibrada e sem campos que identifiquem."""

import subprocess
import sys
from dataclasses import fields

from marketing_agent.evals.blind import BlindSide, blind_side, path_for_side, side_a_path


def test_side_assignment_reproducible_from_seed() -> None:
    first = [side_a_path(1234, f"b{i}") for i in range(20)]
    assert first == [side_a_path(1234, f"b{i}") for i in range(20)]
    code = "from marketing_agent.evals.blind import side_a_path; print(side_a_path(1234, 'b7'))"
    other = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        env={"PYTHONHASHSEED": "99"},
    )  # fmt: skip
    assert other.stdout.strip() == first[7]


def test_side_assignment_balanced() -> None:
    share = sum(side_a_path(seed, "pao") == "simples" for seed in range(1000)) / 1000
    assert 0.45 <= share <= 0.55


def test_sides_are_complementary() -> None:
    for seed in range(50):
        assert {path_for_side(seed, "x", "a"), path_for_side(seed, "x", "b")} == {
            "simples",
            "especializado",
        }


def test_blind_side_has_no_identifying_fields() -> None:
    names = {field.name for field in fields(BlindSide)}
    assert names == {
        "legenda", "hashtags", "apelo_a_acao", "alegacoes", "alt_text", "is_ai_generated",
    }  # fmt: skip
    snapshot = {
        "texto": {
            "legenda": "Ola", "hashtags": ["#a"], "apelo_a_acao": "Venha", "alt_text": "x",
            "alegacoes": [{"texto": "Fresco", "kb_ref": "id-1"}, {"texto": "Bom", "kb_ref": None}],
        },
        "alt_text": "Foto",
        "is_ai_generated": True,
        "review": {"verdict": "rever", "violations": ["voz_incoerente"], "notes": "segredo"},
    }  # fmt: skip
    side = blind_side(snapshot)
    assert [c.has_support for c in side.alegacoes] == [True, False]
    assert "segredo" not in repr(side) and "id-1" not in repr(side)
