"""Leitura de segredos a partir de ficheiros, sem os expor em variaveis de ambiente."""

from pathlib import Path


class SecretFileError(Exception):
    """O ficheiro de segredo nao pode ser lido ou esta vazio."""


def read_secret_file(path: Path) -> str:
    """Le o segredo em `path` e remove espacos nas pontas.

    Raises:
        SecretFileError: se o ficheiro nao for legivel ou ficar vazio apos o strip.
            A mensagem nomeia o caminho, nunca o conteudo.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SecretFileError(f"nao foi possivel ler o ficheiro de segredo: {path}") from error
    secret = content.strip()
    if not secret:
        raise SecretFileError(f"ficheiro de segredo vazio: {path}")
    return secret
