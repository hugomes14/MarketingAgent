"""Comando `marketing-agent`: operacao local (marca, precos, reservas, migracoes e painel).

Codigos de saida: 0 sucesso; 2 erro de uso ou de configuracao (uma linha em stderr, sem
traceback); 1 erro inesperado. Os segredos nunca sao impressos.
"""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path

import uvicorn
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError

from marketing_agent.brands import ensure_brand
from marketing_agent.budget.pricing import ModelPrice, load_model_prices, sync_model_prices
from marketing_agent.cli_parser import build_parser
from marketing_agent.clock import SystemClock
from marketing_agent.dev_proxy import DevProxyServer, build_dev_proxy
from marketing_agent.loopback import is_loopback_host, parse_loopback_endpoint
from marketing_agent.startup import (
    StartupError,
    build_budget_controller,
    database_error_message,
    load_settings,
    open_database,
)

PANEL_FACTORY = "marketing_agent.panel.main:create_app_from_env"
FORWARDED_ALLOW_IPS = "FORWARDED_ALLOW_IPS"

Command = Callable[[argparse.Namespace], int]


def find_repository_root() -> Path:
    """Raiz do repositorio: o pai da pasta `src` que contem este pacote."""
    for parent in Path(__file__).resolve().parents:
        if parent.name == "src":
            return parent.parent
    raise StartupError("nao encontrei a raiz do repositorio (pasta src): o pacote nao esta em src/")


def cmd_create_brand(args: argparse.Namespace) -> int:
    """Cria a marca (idempotente por nome) e imprime o seu id."""
    with open_database(load_settings()) as session_factory:
        brand_id, created = ensure_brand(session_factory, args.name)
    print(brand_id)
    print("marca criada" if created else "a marca ja existia", file=sys.stderr)
    return 0


def _load_all_prices(paths: Sequence[Path]) -> list[ModelPrice]:
    prices: list[ModelPrice] = []
    for path in paths:
        try:
            prices.extend(load_model_prices(path))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise StartupError(f"ficheiro de precos {path}: {exc}") from exc
    return prices


def cmd_sync_prices(args: argparse.Namespace) -> int:
    """Sincroniza os ficheiros de precos (por omissao o de `MODEL_PRICES_PATH`) com a base."""
    settings = load_settings()
    prices = _load_all_prices(args.file or [settings.model_prices_path])
    with open_database(settings) as session_factory:
        try:
            sync_model_prices(session_factory, prices)
        except ValueError as exc:
            raise StartupError(str(exc)) from exc
    print(f"{len(prices)} precos sincronizados")
    return 0


def cmd_recover_reservations(args: argparse.Namespace) -> int:
    """Marca como `unknown_outcome` as reservas abertas ha mais de N minutos."""
    settings = load_settings()
    with open_database(settings) as session_factory:
        budget = build_budget_controller(settings, session_factory, SystemClock())
        count = budget.recover_stale(timedelta(minutes=args.older_than_minutes))
    print(f"{count} reserva(s) marcada(s) como unknown_outcome")
    return 0


def _alembic_config(root: Path) -> Config:
    ini, scripts = root / "alembic.ini", root / "migrations"
    if not ini.is_file() or not scripts.is_dir():
        raise StartupError(
            f"alembic.ini e migrations/ nao encontrados em {root}: "
            "`migrate` exige o repositorio (instalacao editavel com `uv sync`)"
        )
    config = Config(str(ini))
    config.set_main_option("script_location", str(scripts))
    return config


def cmd_migrate(args: argparse.Namespace) -> int:
    """Aplica as migracoes ate `head` na base de `DATABASE_URL` (sem efeito se ja estiver)."""
    load_settings()  # falha cedo e com mensagem clara se a configuracao estiver incompleta
    command.upgrade(_alembic_config(find_repository_root()), "head")
    print("migracoes aplicadas (head)")
    return 0


def build_uvicorn_config(host: str, port: int) -> uvicorn.Config:
    """Configuracao do uvicorn para o painel.

    `proxy_headers=False` e fixo e `forwarded_allow_ips` fica por definir: o `request.client`
    nunca pode ser reescrito a partir de `X-Forwarded-For` (ver `panel.auth`).
    """
    return uvicorn.Config(
        PANEL_FACTORY,
        factory=True,
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
    )


def run_server(config: uvicorn.Config) -> None:
    """Carrega o painel (erros de configuracao sobem como `StartupError`) e serve."""
    config.load()
    uvicorn.Server(config).run()


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve o painel com o uvicorn, recusando configuracoes que quebrem a identidade."""
    if FORWARDED_ALLOW_IPS in os.environ:
        raise StartupError(
            f"{FORWARDED_ALLOW_IPS} esta definido: remova-o, pois o painel exige que o servidor "
            "nao reescreva o endereco do cliente a partir de cabecalhos de proxy"
        )
    if not is_loopback_host(args.host):
        if not args.allow_non_loopback:
            raise StartupError(
                f"--host {args.host!r} nao e de loopback: o painel so deve escutar atras do "
                "proxy confiavel; use --allow-non-loopback para o permitir"
            )
        print(f"aviso: o painel escuta em {args.host}, fora do loopback", file=sys.stderr)
    run_server(build_uvicorn_config(args.host, args.port))
    return 0


def serve_forever(server: DevProxyServer) -> None:
    """Serve ate Ctrl+C e liberta a porta."""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_dev_proxy(args: argparse.Namespace) -> int:
    """Proxy de desenvolvimento que injeta o login (so em loopback)."""
    try:
        listen = parse_loopback_endpoint(args.listen, "--listen")
        upstream = parse_loopback_endpoint(args.upstream, "--upstream")
        server = build_dev_proxy(listen, upstream, args.login, args.name)
    except ValueError as exc:
        raise StartupError(str(exc)) from exc
    except OSError as exc:
        raise StartupError(f"nao foi possivel escutar em {args.listen}: {exc.strerror}") from exc
    print(
        "DESENVOLVIMENTO APENAS: quem consegue ligar-se a "
        f"http://{listen[0]}:{listen[1]} age como {args.login!r} no painel.\n"
        f"Reencaminha para {upstream[0]}:{upstream[1]}. Ctrl+C para terminar.",
        file=sys.stderr,
    )
    serve_forever(server)
    return 0


COMMANDS: dict[str, Command] = {
    "create-brand": cmd_create_brand,
    "sync-prices": cmd_sync_prices,
    "recover-reservations": cmd_recover_reservations,
    "migrate": cmd_migrate,
    "serve": cmd_serve,
    "dev-proxy": cmd_dev_proxy,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada: devolve o codigo de saida em vez de terminar o processo."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exit_request:  # argparse sai com 0 (--help) ou 2 (uso invalido)
        return exit_request.code if isinstance(exit_request.code, int) else 2
    try:
        return COMMANDS[args.command](args)
    except StartupError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"erro: {database_error_message(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"erro inesperado: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
