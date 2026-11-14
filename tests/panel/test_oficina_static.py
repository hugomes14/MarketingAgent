"""Licencas, hashes, imports e CSP dos modulos locais da oficina."""

import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src/marketing_agent/panel/static"
VENDOR = STATIC / "vendor/three"
JS_FORBIDDEN = (
    r"\binnerHTML\b",
    r"\bouterHTML\b",
    r"\binsertAdjacentHTML\b",
    r"document\.write\s*\(",
    r"\beval\s*\(",
    r"new\s+Function\b",
)


def iter_static_js(root):
    return sorted(root.rglob("*.js"))


def strip_comments(source):
    # Testa codigo executavel, nao comentarios de documentacao ou URLs em strings.
    return re.sub(r"/\*.*?\*/|(?m:^\s*//[^\n]*)", "", source, flags=re.S)


def rows():
    text = (VENDOR / "README.md").read_text()
    return re.findall(
        r"^\| `([^`]+)` \| ([a-f0-9]{64}) \| ([a-f0-9]{64}) \| (?:sim|nao) \|$", text, re.M
    )


def test_three_files_match_recorded_sha256():
    entries = rows()
    assert len(entries) == 18  # LICENSE, dois builds e os quinze addons da lista do plano.
    assert {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file()} == {
        "README.md",
        *(p for p, _, _ in entries),
    }
    for path, _, digest in entries:
        assert hashlib.sha256((VENDOR / path).read_bytes()).hexdigest() == digest, path


def test_three_version_is_reference_version():
    assert "**0.186.1**" in (VENDOR / "README.md").read_text()


def test_three_license_is_mit_and_present():
    assert "MIT License" in (VENDOR / "LICENSE").read_text()
    assert "Permission is hereby granted" in (VENDOR / "LICENSE").read_text()


def test_vendor_imports_relative_and_resolve():
    files = iter_static_js(VENDOR)
    assert files
    for file in files:
        for spec in re.findall(r'\bfrom\s+[\'"]([^\'"]+)[\'"]', strip_comments(file.read_text())):
            assert spec.startswith(("./", "../")), (file, spec)
            assert (file.parent / spec).is_file(), (file, spec)


def test_no_forbidden_patterns_in_vendored_js():
    files = iter_static_js(VENDOR)
    assert files
    for file in files:
        for pattern in JS_FORBIDDEN:
            assert not re.search(pattern, strip_comments(file.read_text())), (file, pattern)


def test_js_served_with_javascript_mime_and_csp(make_panel):
    response = make_panel([]).get("/static/vendor/three/build/three.module.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        response.headers["content-security-policy"]
        == "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
    )
