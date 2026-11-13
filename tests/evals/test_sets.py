"""Conjunto de avaliacao versionado (Plano 2C, T4): carregador puro, estrito e de erros fixos."""

import copy
import os
from pathlib import Path

import pytest
import yaml

from marketing_agent.config import Settings
from marketing_agent.evals.sets import (
    MAX_SET_BYTES,
    EvalBrief,
    EvalSet,
    EvalSetError,
    load_eval_set,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "evals"
REPO_EVALS = Path(__file__).resolve().parents[2] / "evals"
SECRET = "SEGREDO-NAO-ECOAR"


def brief(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "key": "b1", "tema": "Tema", "pilar": "Pilar", "objetivo": "Objetivo",
        "canais": ["instagram"], "kb_refs": [], "tags": [],
    }  # fmt: skip
    return {**base, **changes}


def write_set(root: Path, document: object, name: str = "conj") -> Path:
    path = root / f"{name}.yaml"
    path.write_text(
        document if isinstance(document, str) else yaml.safe_dump(document), encoding="utf-8"
    )
    return path


def valid_document(**changes: object) -> dict[str, object]:
    return {"name": "conj", "version": 1, "briefs": [brief()], **changes}


def error_of(root: Path, name: str = "conj") -> str:
    with pytest.raises(EvalSetError) as caught:
        load_eval_set(name, root)
    return str(caught.value)


def test_load_valid_set() -> None:
    loaded = load_eval_set("briefs_teste", FIXTURES)

    assert isinstance(loaded, EvalSet)
    assert (loaded.name, loaded.version) == ("briefs_teste", 1)
    assert [item.key for item in loaded.briefs] == ["pao_do_dia", "oferta_teste"]
    first, second = loaded.briefs
    assert first.canais == ["instagram"] and first.tags == []
    assert second.canais == ["instagram", "facebook"] and second.tags == ["com_preco"]
    assert second.kb_refs == ["kb-teste-1"]


def test_models_are_frozen_and_forbid_extra_fields() -> None:
    loaded = load_eval_set("briefs_teste", FIXTURES)
    with pytest.raises(ValueError):
        loaded.briefs[0].tema = "outro"  # type: ignore[misc]
    with pytest.raises(ValueError):
        loaded.name = "outro"  # type: ignore[misc]
    with pytest.raises(ValueError):
        EvalBrief.model_validate({**brief(), "extra": 1})


def test_duplicate_keys_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, valid_document(briefs=[brief(), brief(tema="Outro")]))
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: briefs (chaves_repetidas)"


def test_name_mismatch_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, valid_document(name="outro"))
    assert error_of(tmp_path) == "o campo name do conjunto nao corresponde ao nome pedido"


def test_unknown_field_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, valid_document(extra=SECRET))
    message = error_of(tmp_path)
    assert message == "conjunto de avaliacao invalido: (raiz) (campo desconhecido)"
    write_set(tmp_path, valid_document(briefs=[brief(**{SECRET: 1})]))
    assert SECRET not in error_of(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "../x", "a/b", "/etc/passwd", "a\x00b", "..", ".", "", "Briefs", "BRIEFS_V1", "briefs.v1",
        "briefs_v1\n", "briefs v1", "x" * 41, "brіefs", "ｂriefs", "a\\b", "briefs_v1.yaml",
    ],
)  # fmt: skip
def test_set_name_traversal_rejected(tmp_path: Path, name: str) -> None:
    (tmp_path / "x.yaml").write_text("name: x\n", encoding="utf-8")
    assert error_of(tmp_path, name) == "nome de conjunto invalido"


def test_non_string_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(EvalSetError, match="nome de conjunto invalido"):
        load_eval_set(None, tmp_path)  # type: ignore[arg-type]


def test_invalid_name_is_rejected_before_touching_the_filesystem(tmp_path: Path) -> None:
    missing = tmp_path / "nao_existe"
    assert error_of(missing, "../x") == "nome de conjunto invalido"


def test_missing_file_and_missing_root(tmp_path: Path) -> None:
    assert error_of(tmp_path, "inexistente") == "conjunto de avaliacao nao encontrado"
    assert error_of(tmp_path / "nao_existe") == "conjunto de avaliacao nao encontrado"


def test_directory_named_like_a_set_is_not_a_set(tmp_path: Path) -> None:
    (tmp_path / "conj.yaml").mkdir()
    assert error_of(tmp_path) == "conjunto de avaliacao nao encontrado"


def test_symlink_escaping_root_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    write_set(outside, valid_document(), name="conj")
    root = tmp_path / "raiz"
    root.mkdir()
    os.symlink(outside / "conj.yaml", root / "conj.yaml")
    assert error_of(root) == "conjunto de avaliacao fora do diretorio dos conjuntos"


def test_symlink_inside_root_is_accepted(tmp_path: Path) -> None:
    write_set(tmp_path, valid_document(), name="real")
    os.symlink(tmp_path / "real.yaml", tmp_path / "conj.yaml")
    assert load_eval_set("conj", tmp_path).name == "conj"


def test_oversize_file_rejected_before_parsing(tmp_path: Path) -> None:
    write_set(tmp_path, "# " + "x" * MAX_SET_BYTES + "\n")
    assert error_of(tmp_path) == "conjunto de avaliacao demasiado grande"


def test_file_at_the_size_cap_is_still_parsed(tmp_path: Path) -> None:
    body = yaml.safe_dump(valid_document())
    padding = "#" * (MAX_SET_BYTES - len(body.encode()) - 1) + "\n"
    write_set(tmp_path, padding + body)
    assert load_eval_set("conj", tmp_path).name == "conj"


def test_alias_bomb_rejected_without_expanding(tmp_path: Path) -> None:
    bomb = (
        "name: conj\nversion: 1\n"
        "a: &a [x, x, x, x, x, x, x, x, x]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "d: [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
        "briefs: []\n"
    )
    write_set(tmp_path, bomb)
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: YAML com aliases ou ancoras"


def test_a_single_alias_is_also_rejected(tmp_path: Path) -> None:
    text = yaml.safe_dump(valid_document()).replace("tema: Tema", "tema: &t Tema\n  objetivo2: *t")
    write_set(tmp_path, text)
    assert "aliases" in error_of(tmp_path)


def test_duplicate_yaml_mapping_keys_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, "name: conj\nversion: 1\nversion: 2\nbriefs: []\n")
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: YAML com chaves repetidas"


def test_unsafe_yaml_tags_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, "name: !!python/object/apply:os.getcwd []\nversion: 1\nbriefs: []\n")
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: YAML ilegivel"


def test_deeply_nested_yaml_rejected_with_a_fixed_error(tmp_path: Path) -> None:
    write_set(tmp_path, "name: " + "[" * 20000 + "]" * 20000 + "\n")
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: YAML ilegivel"


@pytest.mark.parametrize(
    "text", ["", "[]", "- a\n", "just text\n", "a: [unclosed\n", "---\na: 1\n---\nb: 2\n"]
)
def test_non_mapping_or_broken_yaml_rejected(tmp_path: Path, text: str) -> None:
    write_set(tmp_path, text)
    assert error_of(tmp_path).startswith("conjunto de avaliacao invalido: ")


def test_non_utf8_file_rejected(tmp_path: Path) -> None:
    (tmp_path / "conj.yaml").write_bytes(b"name: \xff\xfe\n")
    assert error_of(tmp_path) == "conjunto de avaliacao invalido: YAML ilegivel"


@pytest.mark.parametrize("version", [0, -1, "1", 1.5, None, True])
def test_bad_version_rejected(tmp_path: Path, version: object) -> None:
    write_set(tmp_path, valid_document(version=version))
    assert error_of(tmp_path).startswith("conjunto de avaliacao invalido: version")


def test_brief_count_bounds(tmp_path: Path) -> None:
    write_set(tmp_path, valid_document(briefs=[]))
    assert error_of(tmp_path).startswith("conjunto de avaliacao invalido: briefs")
    twenty = [brief(key=f"b{i}") for i in range(20)]
    write_set(tmp_path, valid_document(briefs=twenty))
    assert len(load_eval_set("conj", tmp_path).briefs) == 20
    write_set(tmp_path, valid_document(briefs=[*twenty, brief(key="b20")]))
    assert error_of(tmp_path).startswith("conjunto de avaliacao invalido: briefs")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"key": "Maiuscula"}, "key"), ({"key": "com-hifen"}, "key"), ({"key": ""}, "key"),
        ({"key": "a" * 41}, "key"), ({"key": 7}, "key"), ({"key": "ab\n"}, "key"),
        ({"tema": ""}, "tema"), ({"tema": "   "}, "tema"), ({"tema": "t" * 301}, "tema"),
        ({"objetivo": "o" * 301}, "objetivo"), ({"pilar": ""}, "pilar"),
        ({"pilar": "p" * 201}, "pilar"),
        ({"canais": []}, "canais"), ({"canais": ["tiktok"]}, "canais"),
        ({"canais": ["instagram", "instagram"]}, "canais"),
        ({"canais": ["instagram", "facebook", "instagram"]}, "canais"),
        ({"kb_refs": [f"k{i}" for i in range(21)]}, "kb_refs"),
        ({"kb_refs": ["k" * 65]}, "kb_refs"), ({"kb_refs": [""]}, "kb_refs"),
        ({"kb_refs": ["a", "a"]}, "kb_refs"),
        ({"tags": ["inventada"]}, "tags"), ({"tags": ["com_preco", "com_preco"]}, "tags"),
    ],
)  # fmt: skip
def test_invalid_brief_fields_rejected(
    tmp_path: Path, changes: dict[str, object], field: str
) -> None:
    write_set(tmp_path, valid_document(briefs=[brief(**changes)]))
    assert error_of(tmp_path).startswith(f"conjunto de avaliacao invalido: briefs.0.{field}")


def test_limits_at_the_boundary_are_accepted(tmp_path: Path) -> None:
    edge = brief(
        key="a" * 40, tema="t" * 300, objetivo="o" * 300, pilar="p" * 200,
        canais=["facebook", "instagram"], kb_refs=["k" * 64] * 1 + [f"r{i}" for i in range(19)],
        tags=["com_preco", "sem_foto_adequada", "promessa_exagerada"],
    )  # fmt: skip
    write_set(tmp_path, valid_document(briefs=[edge]))
    assert load_eval_set("conj", tmp_path).briefs[0].key == "a" * 40


def test_error_messages_never_echo_values(tmp_path: Path) -> None:
    hostile = [
        valid_document(briefs=[brief(tema=SECRET * 100)]),
        valid_document(briefs=[brief(key=SECRET)]),
        valid_document(briefs=[brief(canais=[SECRET])]),
        valid_document(briefs=[brief(tags=[SECRET])]),
        valid_document(briefs=[brief(**{SECRET: SECRET})]),
        valid_document(version=SECRET),
        valid_document(name=SECRET),
        {SECRET: SECRET},
        f"name: [{SECRET}\n",
        f"{SECRET}\n",
    ]
    for document in hostile:
        write_set(tmp_path, document)
        assert SECRET not in error_of(tmp_path)


def test_error_list_is_bounded(tmp_path: Path) -> None:
    many = [brief(key=f"B{i}") for i in range(20)]
    write_set(tmp_path, valid_document(briefs=many))
    assert len(error_of(tmp_path)) < 400


def test_loader_does_not_modify_the_document_or_leak_state(tmp_path: Path) -> None:
    document = valid_document()
    snapshot = copy.deepcopy(document)
    write_set(tmp_path, document)
    assert load_eval_set("conj", tmp_path) == load_eval_set("conj", tmp_path)
    assert document == snapshot


def test_briefs_v1_has_8_briefs_and_required_tags() -> None:
    loaded = load_eval_set("briefs_v1", REPO_EVALS)

    assert (loaded.name, loaded.version) == ("briefs_v1", 1)
    assert len(loaded.briefs) == 8
    assert len({item.pilar for item in loaded.briefs}) >= 3
    tags = {tag for item in loaded.briefs for tag in item.tags}
    assert tags == {"com_preco", "sem_foto_adequada", "promessa_exagerada"}


def test_briefs_v1_is_a_marked_draft_without_invented_prices() -> None:
    text = (REPO_EVALS / "briefs_v1.yaml").read_text(encoding="utf-8")
    assert text.startswith("# RASCUNHO: rever pelos pilares da estratégia ativa")
    assert "€" not in text and "EUR" not in text and "$" not in text
    loaded = load_eval_set("briefs_v1", REPO_EVALS)
    assert all(not any(ch.isdigit() for ch in item.tema + item.objetivo) for item in loaded.briefs)


def test_evals_dir_defaults_to_evals_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5433/db")
    monkeypatch.delenv("EVALS_DIR", raising=False)
    assert Settings(_env_file=None).evals_dir == Path("evals")  # type: ignore[call-arg]
    monkeypatch.setenv("EVALS_DIR", "/tmp/outros")
    assert Settings(_env_file=None).evals_dir == Path("/tmp/outros")  # type: ignore[call-arg]
