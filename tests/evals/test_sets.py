"""Conjuntos de avaliacao versionados: validacao, nomes seguros e o conjunto briefs_v1."""

from pathlib import Path

import pytest

from marketing_agent.evals.sets import EvalSetError, check_set_name, load_eval_set

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "evals"

VALID = """
name: conjunto
version: 2
briefs:
  - key: a
    tema: Tema A
    pilar: Pilar
    objetivo: Objetivo
    canais: [instagram]
  - key: b
    tema: Tema B
    pilar: Pilar
    objetivo: Objetivo
    canais: [facebook, instagram]
    kb_refs: ["preco:pao"]
    tags: [com_preco]
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_load_valid_set(tmp_path: Path) -> None:
    eval_set = load_eval_set("conjunto", write(tmp_path, "conjunto", VALID))
    assert (eval_set.name, eval_set.version) == ("conjunto", 2)
    assert [brief.key for brief in eval_set.briefs] == ["a", "b"]
    assert eval_set.briefs[1].kb_refs == ["preco:pao"]
    assert eval_set.briefs[0].tags == []


def test_fixture_set_is_valid() -> None:
    assert load_eval_set("briefs_teste", FIXTURES).version == 3


def test_duplicate_keys_rejected(tmp_path: Path) -> None:
    body = VALID.replace("key: b", "key: a")
    with pytest.raises(EvalSetError, match="briefs"):
        load_eval_set("conjunto", write(tmp_path, "conjunto", body))


def test_name_mismatch_rejected(tmp_path: Path) -> None:
    with pytest.raises(EvalSetError, match="name do ficheiro"):
        load_eval_set("outro", write(tmp_path, "outro", VALID))


def test_unknown_field_rejected(tmp_path: Path) -> None:
    body = VALID.replace("    tema: Tema A", "    tema: Tema A\n    preco: 3")
    with pytest.raises(EvalSetError, match="extra_forbidden"):
        load_eval_set("conjunto", write(tmp_path, "conjunto", body))


@pytest.mark.parametrize("bad", ["../x", "a/b", "A", "", "x" * 41, "nome.yaml"])
def test_set_name_traversal_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(EvalSetError, match="nome de conjunto invalido") as info:
        load_eval_set(bad, tmp_path)
    assert bad not in str(info.value) or bad == ""
    with pytest.raises(EvalSetError):
        check_set_name(bad)


def test_missing_and_broken_files(tmp_path: Path) -> None:
    with pytest.raises(EvalSetError, match="inexistente"):
        load_eval_set("nada", tmp_path)
    with pytest.raises(EvalSetError, match="YAML invalido"):
        load_eval_set("partido", write(tmp_path, "partido", "name: [x"))


@pytest.mark.parametrize(
    "replacement",
    [
        ('kb_refs: ["preco:pao"]', 'kb_refs: ["sem-dois-pontos"]'),
        ("tags: [com_preco]", "tags: [inventada]"),
        ("canais: [facebook, instagram]", "canais: [tiktok]"),
        ("version: 2", "version: 0"),
    ],
)
def test_invalid_values_rejected(tmp_path: Path, replacement: tuple[str, str]) -> None:
    body = VALID.replace(*replacement)
    with pytest.raises(EvalSetError):
        load_eval_set("conjunto", write(tmp_path, "conjunto", body))


def test_briefs_v1_has_8_briefs_and_required_tags() -> None:
    eval_set = load_eval_set("briefs_v1", REPO_ROOT / "evals")
    assert len(eval_set.briefs) == 8
    assert len({brief.pilar for brief in eval_set.briefs}) >= 3
    tags = {tag for brief in eval_set.briefs for tag in brief.tags}
    assert tags == {"com_preco", "sem_foto_adequada", "promessa_exagerada"}
    priced = [brief for brief in eval_set.briefs if "com_preco" in brief.tags]
    assert all(any(ref.startswith("preco:") for ref in brief.kb_refs) for brief in priced)
