"""Integridade, selecao segura e reescrita conservadora do fornecedor Three."""

import base64
import hashlib
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/vendor_three.py"
spec = importlib.util.spec_from_file_location("vendor_three", SCRIPT)
assert spec and spec.loader
vendor = importlib.util.module_from_spec(spec)
sys.modules["vendor_three"] = vendor
spec.loader.exec_module(vendor)
VendoredFile = vendor.VendoredFile
readme_table = vendor.readme_table
rewrite_three_imports = vendor.rewrite_three_imports
select_members = vendor.select_members
verify_integrity = vendor.verify_integrity


def test_integrity_accepts_published_and_rejects_tampered():
    payload = b"package fixture"
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()
    verify_integrity(payload, integrity)
    for data, value in (
        (payload + b"x", integrity),
        (payload, "sha256-x"),
        (payload, "sha512-invalid!"),
    ):
        with pytest.raises(ValueError):
            verify_integrity(data, value)


def test_select_members_rejects_traversal_and_extras():
    wanted = {"package/build/three.module.js"}
    assert select_members([*wanted, "package/other.js"], wanted) == sorted(wanted)
    for bad in ("../escape", "/absolute", "package/../escape", "package\\escape"):
        with pytest.raises(ValueError):
            select_members([*wanted, bad], wanted)
    with pytest.raises(ValueError):
        select_members([], wanted)
    with pytest.raises(ValueError):
        select_members([*wanted, *wanted], wanted)


def test_rewrite_only_bare_three_statements():
    source = """/** @three_import import { Mesh } from 'three'; */
// import { Mesh } from 'three';
import {
  Mesh,
} from 'three';
export {Mesh} from "three";
import {Pass} from './Pass.js';
const example = "import X from 'three';";
const template = `
import X from 'three';
`;
"""
    expected = source.replace("} from 'three';", "} from '../build/three.module.js';").replace(
        'from "three";', 'from "../build/three.module.js";'
    )
    # A substituicao de expectativa inclui o comentario; repor explicitamente esse dado.
    expected = expected.replace(
        "@three_import import { Mesh } from '../build/three.module.js';",
        "@three_import import { Mesh } from 'three';",
    ).replace(
        "// import { Mesh } from '../build/three.module.js';", "// import { Mesh } from 'three';"
    )
    assert rewrite_three_imports(source, "../build/three.module.js") == expected


def test_readme_table_lists_both_hashes():
    row = VendoredFile("build/a.js", "a" * 64, "b" * 64, True)
    table = readme_table([row])
    assert row.path in table and row.origin_sha256 in table and row.local_sha256 in table


def test_archive_links_are_rejected(tmp_path):
    prepare_files = vendor.prepare_files

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("package/LICENSE")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ValueError):
        prepare_files(stream.getvalue(), wanted={"LICENSE"})


def test_build_files_have_relative_target_in_same_directory(tmp_path):
    rows = vendor._write_files(tmp_path, {"build/three.core.js": b"export const version = 1;\n"})
    assert (tmp_path / "build/three.core.js").read_bytes() == b"export const version = 1;\n"
    assert not rows[0].rewritten


def test_cli_preserves_existing_destination_without_network(tmp_path, monkeypatch):
    marker = tmp_path / "existing.txt"
    marker.write_text("preservar")

    def forbidden():
        raise AssertionError("nao deve descarregar antes de validar destino")

    monkeypatch.setattr(vendor, "_verified_download", forbidden)
    assert vendor.main(["--dest", str(tmp_path)]) == 2
    assert marker.read_text() == "preservar"


def test_exported_string_is_data_not_export_from_statement():
    source = "export const example = \"from 'three'\";\n"
    assert rewrite_three_imports(source, "../build/three.module.js") == source
