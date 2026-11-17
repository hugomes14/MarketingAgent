"""Copia um subconjunto verificado do Three.js, sem executar codigo do pacote npm."""

import argparse
import base64
import binascii
import hashlib
import io
import json
import posixpath
import re
import sys
import tarfile
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.request import urlopen

VERSION = "0.186.1"
INTEGRITY = (
    "sha512-blFeqb49wRCSGUGj7gtpfnSGHy2lwDk94RhUmS1c/hTby70kvChbWpkJ4Pm1390"
    "LqzzvTmzgXKHPEafJwCb8jA=="
)
TARBALL = f"https://registry.npmjs.org/three/-/three-{VERSION}.tgz"
VENDORED_FILES = frozenset(
    {
        "LICENSE",
        "build/three.module.js",
        "build/three.core.js",
        "examples/jsm/controls/OrbitControls.js",
        "examples/jsm/renderers/CSS2DRenderer.js",
        "examples/jsm/environments/RoomEnvironment.js",
        *(
            f"examples/jsm/postprocessing/{name}.js"
            for name in (
                "EffectComposer",
                "RenderPass",
                "GTAOPass",
                "OutputPass",
                "ShaderPass",
                "MaskPass",
                "Pass",
            )
        ),
        *(
            f"examples/jsm/shaders/{name}.js"
            for name in ("CopyShader", "GTAOShader", "PoissonDenoiseShader", "OutputShader")
        ),
        "examples/jsm/math/SimplexNoise.js",
    }
)
MAX_DOWNLOAD = 30 * 1024 * 1024


@dataclass(frozen=True)
class VendoredFile:
    """Proveniencia de cada ficheiro copiado, com hashes de origem e resultado."""

    path: str
    origin_sha256: str
    local_sha256: str
    rewritten: bool


def verify_integrity(data: bytes, integrity: str) -> None:
    """Exige sha512 base64 valido e exatamente o digest do arquivo recebido."""
    try:
        algorithm, encoded = integrity.split("-", 1)
        expected = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("integridade sha512 invalida") from None
    if algorithm != "sha512" or hashlib.sha512(data).digest() != expected:
        raise ValueError("integridade sha512 nao corresponde ao arquivo")


def select_members(names: Iterable[str], wanted: Collection[str]) -> list[str]:
    """Seleciona nomes exatos, recusando caminhos perigosos, duplicados e omissoes."""
    selected: set[str] = set()
    for name in names:
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise ValueError("caminho inseguro no arquivo")
        if name in wanted:
            if name in selected:
                raise ValueError("ficheiro duplicado no arquivo")
            selected.add(name)
    if selected != set(wanted):
        raise ValueError("arquivo incompleto para os ficheiros exigidos")
    return sorted(selected)


def _mask_noncode(source: str) -> str:
    """Mascara comentarios e templates preservando offsets; mantem strings de imports."""
    chars = list(source)
    index = 0
    while index < len(source):
        if source[index] in ('"', "'"):
            quote = source[index]
            index += 1
            while index < len(source) and source[index] != quote:
                index += 2 if source[index] == "\\" else 1
            index += 1
        elif (
            source.startswith("//", index) or source.startswith("/*", index) or source[index] == "`"
        ):
            start = index
            if source.startswith("//", index):
                end = source.find("\n", index)
                index = len(source) if end < 0 else end
            elif source.startswith("/*", index):
                end = source.find("*/", index + 2)
                index = len(source) if end < 0 else end + 2
            else:
                index += 1
                while index < len(source) and source[index] != "`":
                    index += 2 if source[index] == "\\" else 1
                index += 1
            for position in range(start, min(index, len(source))):
                if chars[position] != "\n":
                    chars[position] = " "
        else:
            index += 1
    return "".join(chars)


def rewrite_three_imports(source: str, target: str) -> str:
    """Reescreve apenas especificadores nus three em instrucoes import/export estaticas."""
    if not target.startswith(("./", "../")) or not re.fullmatch(r"[./A-Za-z0-9_-]+", target):
        raise ValueError("destino de import deve ser caminho relativo seguro")
    pattern = r"""(?m)^[ \t]*(?:import|export)\s+[A-Za-z0-9_$\s,{}*]+?\bfrom\s+(['"])three\1"""
    matches = list(re.finditer(pattern, _mask_noncode(source)))
    for match in reversed(matches):
        start = match.end() - len("three") - 1
        source = source[:start] + target + source[start + len("three") :]
    return source


def readme_table(rows: Sequence[VendoredFile]) -> str:
    """Tabela Markdown estavel, legivel por humanos e pelos testes de integridade."""
    lines = ["| Ficheiro | SHA256 origem | SHA256 local | Reescrito |", "| --- | --- | --- | --- |"]
    for row in rows:
        changed = "sim" if row.rewritten else "nao"
        lines.append(f"| `{row.path}` | {row.origin_sha256} | {row.local_sha256} | {changed} |")
    return "\n".join(lines)


def prepare_files(data: bytes, *, wanted: Collection[str] = VENDORED_FILES) -> dict[str, bytes]:
    """Le dados de ficheiros regulares permitidos, sem extrair caminhos do tar para disco."""
    names = {f"package/{name}" for name in wanted}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if any(member.issym() or member.islnk() for member in members):
            raise ValueError("ligacoes nao permitidas no arquivo")
        selected = select_members((member.name for member in members), names)
        result: dict[str, bytes] = {}
        for name in selected:
            member = archive.getmember(name)
            if not member.isfile() or member.size > MAX_DOWNLOAD:
                raise ValueError("tipo ou tamanho invalido no arquivo")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("ficheiro nao pode ser lido")
            result[name.removeprefix("package/")] = stream.read(MAX_DOWNLOAD + 1)
    return result


def _download(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        if not response.geturl().startswith("https://registry.npmjs.org/"):
            raise ValueError("origem final fora do registo npm")
        body: bytes = response.read(MAX_DOWNLOAD + 1)
    if len(body) > MAX_DOWNLOAD:
        raise ValueError("resposta npm demasiado grande")
    return body


def _verified_download() -> bytes:
    # JSON e uma fronteira externa: validar campos antes de os usar como contrato.
    metadata: dict[str, object] = json.loads(
        _download(f"https://registry.npmjs.org/three/{VERSION}")
    )
    dist = metadata.get("dist")
    if (
        not isinstance(dist, dict)
        or dist.get("integrity") != INTEGRITY
        or dist.get("tarball") != TARBALL
    ):
        raise ValueError("metadados npm diferem da versao e integridade fixadas")
    data = _download(TARBALL)
    verify_integrity(data, INTEGRITY)
    return data


def _write_files(dest: Path, files: dict[str, bytes]) -> list[VendoredFile]:
    rows = []
    for name, original in sorted(files.items()):
        local = original
        if name.endswith(".js"):
            target = posixpath.relpath("build/three.module.js", str(PurePosixPath(name).parent))
            if not target.startswith("."):
                target = "./" + target
            local = rewrite_three_imports(original.decode("utf-8"), target).encode("utf-8")
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(local)
        rows.append(
            VendoredFile(
                name,
                hashlib.sha256(original).hexdigest(),
                hashlib.sha256(local).hexdigest(),
                local != original,
            )
        )
    return rows


def _readme(rows: Sequence[VendoredFile]) -> str:
    return f"""# Three.js local

Versao **{VERSION}**; licenca MIT em LICENSE.

Origem: {TARBALL}

Integridade npm publicada e fixada: `{INTEGRITY}`. Verificada antes de ler o tar.

A unica alteracao e reescrever from 'three' nas instrucoes import/export para
caminhos relativos ao build/three.module.js local. Comentarios e restante codigo intactos.

{readme_table(rows)}

## Como atualizar

Confirmar a versao da referencia e a integridade no registo npm; atualizar constantes
e lista de ficheiros em scripts/vendor_three.py, rever a cadeia de imports e os testes.
Gerar numa pasta vazia com --dest, rever o diff e hashes antes de substituir esta pasta.
Executar pytest tests/test_vendor_three.py tests/panel/test_oficina_static.py.
Nao sao executados scripts npm e nao e necessario Node.
"""


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: saida0 sucesso,2 argumentos/validacao/rede; nao sobrescreve pasta preenchida."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=[VERSION], default=VERSION)
    parser.add_argument(
        "--dest", type=Path, required=True, help="diretorio novo ou vazio; nunca sobrescreve"
    )
    args = parser.parse_args(argv)
    try:
        if args.dest.exists() and (not args.dest.is_dir() or any(args.dest.iterdir())):
            raise ValueError("destino deve ser diretorio novo ou vazio")
        files = prepare_files(_verified_download())
        args.dest.mkdir(parents=True, exist_ok=True)
        rows = _write_files(args.dest, files)
        (args.dest / "README.md").write_text(_readme(rows), encoding="utf-8")
    except (ValueError, OSError, URLError, tarfile.TarError) as exc:
        print(f"vendor_three: importacao falhou ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    print(f"Three.js {VERSION}: {len(rows)} ficheiros verificados em {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
