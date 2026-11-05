"""Leitura de segredos a partir de ficheiros, sem os expor em variaveis de ambiente."""

from pathlib import Path


class SecretFileError(Exception):
    """O ficheiro de segredo nao pode ser lido, esta vazio ou nao tem o formato esperado."""


def _read_text(path: Path) -> str:
    """Le o ficheiro como UTF-8, aceitando BOM (o que o PowerShell escreve)."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SecretFileError(f"nao foi possivel ler o ficheiro de segredo: {path}") from error
    except UnicodeDecodeError:
        # `from None`: a causa guarda os bytes do ficheiro, ou seja, o segredo.
        raise SecretFileError(f"ficheiro de segredo nao e UTF-8: {path}") from None


def read_secret_file(path: Path) -> str:
    """Le o segredo em `path` e remove espacos nas pontas.

    Raises:
        SecretFileError: se o ficheiro nao for legivel, nao for UTF-8, ficar vazio apos o
            strip ou tiver mais de uma linha. A mensagem nomeia o caminho, nunca o conteudo.
    """
    secret = _read_text(path).strip()
    if not secret:
        raise SecretFileError(f"ficheiro de segredo vazio: {path}")
    if "\n" in secret or "\r" in secret:
        raise SecretFileError(f"ficheiro de segredo com mais de uma linha: {path}")
    return secret
