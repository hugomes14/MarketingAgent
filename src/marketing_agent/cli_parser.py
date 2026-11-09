"""Definicao dos argumentos do comando `marketing-agent` (um subcomando por operacao)."""

import argparse
from pathlib import Path

MAX_PORT = 65535


def _positive_int(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"esperado um inteiro positivo, recebido {text!r}")
    return int(text)


def _port(text: str) -> int:
    value = _positive_int(text)
    if value > MAX_PORT:
        raise argparse.ArgumentTypeError(f"porta fora do intervalo 1-{MAX_PORT}: {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Parser com um subcomando por operacao."""
    parser = argparse.ArgumentParser(
        prog="marketing-agent", description="Operação local do Marketing AI Manager."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMANDO")

    brand = sub.add_parser("create-brand", help="Cria a marca (idempotente por nome).")
    brand.add_argument("name", metavar="NOME", help="Nome da marca.")

    prices = sub.add_parser("sync-prices", help="Sincroniza os preços dos modelos.")
    prices.add_argument(
        "--file",
        type=Path,
        action="append",
        help="Ficheiro YAML de preços (repetível; por omissão MODEL_PRICES_PATH).",
    )

    recover = sub.add_parser(
        "recover-reservations", help="Marca reservas abertas antigas como unknown_outcome."
    )
    recover.add_argument("--older-than-minutes", type=_positive_int, required=True, metavar="N")

    sub.add_parser("migrate", help="Aplica as migrações da base (upgrade head).")

    serve = sub.add_parser("serve", help="Serve o painel (atrás de um proxy confiável).")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=_port, default=8000)
    serve.add_argument(
        "--allow-non-loopback", action="store_true", help="Permite escutar fora do loopback."
    )

    sub.add_parser(
        "worker",
        help="Processa a fila de geração de texto (um job de cada vez) até SIGINT/SIGTERM.",
    )

    proxy = sub.add_parser(
        "dev-proxy", help="Só desenvolvimento: proxy local que injeta a identidade."
    )
    proxy.add_argument("--login", required=True, help="Login que o painel verá (ex.: dono@local).")
    proxy.add_argument("--name", help="Nome de apresentação (opcional).")
    proxy.add_argument("--listen", default="127.0.0.1:8001", metavar="HOST:PORTA")
    proxy.add_argument("--upstream", default="127.0.0.1:8000", metavar="HOST:PORTA")
    return parser
