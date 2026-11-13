"""Comando `marketing-agent`: operacao local (marca, precos, reservas, migracoes e painel).

Codigos de saida: 0 sucesso; 2 erro de uso ou de configuracao (uma linha em stderr, sem
traceback); 1 erro inesperado. Os segredos nunca sao impressos.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import FrameType

import uvicorn
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactStore
from marketing_agent.brands import ensure_brand
from marketing_agent.budget.pricing import (
    ModelPrice,
    PriceBook,
    load_model_prices,
    sync_model_prices,
)
from marketing_agent.cli_parser import build_parser
from marketing_agent.clock import Clock, SystemClock
from marketing_agent.config import IMAGE_ROUTE, Settings
from marketing_agent.content.pipeline import ContentPipeline
from marketing_agent.dev_proxy import DevProxyServer, build_dev_proxy
from marketing_agent.feedback.service import FeedbackService
from marketing_agent.harness.engine import AgentEngine
from marketing_agent.images.factory import ImageBackend, build_image_backend
from marketing_agent.jobs.queue import JobQueue
from marketing_agent.jobs.worker import Worker
from marketing_agent.kb.service import KbService
from marketing_agent.llm.factory import build_llm_router
from marketing_agent.llm.router import LlmRouter
from marketing_agent.loopback import is_loopback_host, parse_loopback_endpoint
from marketing_agent.media.brand_kit import BrandKitService
from marketing_agent.media.library import MediaLibrary
from marketing_agent.media.store import LocalMediaStore, MediaStoreError
from marketing_agent.startup import (
    StartupError,
    build_budget_controller,
    check_content_routes,
    content_job_lease,
    database_error_message,
    load_settings,
    open_database,
    worker_stale_after,
)
from marketing_agent.strategy.service import StrategyService

logger = logging.getLogger(__name__)

PANEL_FACTORY = "marketing_agent.panel.main:create_app_from_env"
FORWARDED_ALLOW_IPS = "FORWARDED_ALLOW_IPS"
# O pacote de 2B corre sempre o Designer (com o gerador falso se nao ha rota `designer_imagem`).
WORKER_ROLES = ("copywriter", "designer", "revisor")
EXIT_INTERRUPTED = 130  # convencao do shell para SIGINT

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


def _build_worker_router(settings: Settings) -> LlmRouter:
    """Router do worker: exige as rotas dos tres papeis e recusa o fornecedor `fake`."""
    missing = [role for role in WORKER_ROLES if role not in settings.role_routes]
    if missing:
        raise StartupError(f"ROLE_ROUTES: faltam as rotas de {', '.join(missing)}")
    if any(route.provider == "fake" for route in settings.role_routes.values()):
        raise StartupError("ROLE_ROUTES: o fornecedor 'fake' so existe nos testes")
    try:
        return build_llm_router(settings)
    except ValueError as exc:
        raise StartupError(str(exc)) from exc


def _build_image_backend(settings: Settings) -> ImageBackend:
    """Gerador de imagens do worker; avisa (so nomes) quando os fundos sao simulados."""
    try:
        backend = build_image_backend(settings)
    except ValueError as exc:  # a mensagem nomeia a variavel, nunca o conteudo da chave
        raise StartupError(str(exc)) from exc
    if IMAGE_ROUTE not in settings.role_routes:
        # Local por omissao (spec 2B, secao 6), mas em producao seria um erro silencioso.
        logger.warning("fundos simulados: sem rota %s", IMAGE_ROUTE)
    return backend


def build_worker(settings: Settings, session_factory: sessionmaker[Session]) -> Worker:
    """Liga motor, servicos, fila (com a lease dos specs) e pipeline num `Worker`."""
    clock = SystemClock()
    budget = build_budget_controller(settings, session_factory, clock)
    store = ArtifactStore(session_factory, clock)
    router = _build_worker_router(settings)
    check_content_routes(settings, PriceBook(session_factory))
    engine = AgentEngine(
        session_factory,
        router,
        budget,
        store,
        clock,
        images=_build_image_backend(settings),
        time_multiplier=settings.llm_time_multiplier,
    )
    queue = JobQueue(session_factory, clock, content_job_lease(settings.llm_time_multiplier))
    library = _build_media_library(settings, session_factory, clock)
    pipeline = ContentPipeline(
        session_factory,
        engine,
        KbService(session_factory, store, clock),
        FeedbackService(session_factory, clock),
        queue,
        clock,
        store,
        StrategyService(session_factory, engine, store, clock),
        library,
        BrandKitService(session_factory, library, clock),
        settings.image_quality,
    )
    older_than = worker_stale_after(settings.llm_time_multiplier)
    return Worker(
        queue,
        pipeline,
        f"worker-{os.getpid()}",
        recover_reservations=lambda: budget.recover_stale(older_than),
    )


def _build_media_library(
    settings: Settings, session_factory: sessionmaker[Session], clock: Clock
) -> MediaLibrary:
    """Biblioteca sobre o mesmo `MEDIA_DIR` do painel; falhas da pasta viram `StartupError`."""
    try:
        return MediaLibrary(session_factory, LocalMediaStore(settings.media_dir), clock)
    except MediaStoreError as exc:
        raise StartupError(f"MEDIA_DIR: {exc}") from exc


@contextmanager
def stop_on_signals(stop: threading.Event) -> Iterator[None]:
    """`SIGINT` e `SIGTERM` ativam `stop` (sem traceback); os tratadores antigos voltam ao sair."""

    handled = (signal.SIGINT, signal.SIGTERM)

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()
        # O 1o sinal pede paragem ordeira; os tratadores antigos voltam ja, para que um 2o sinal
        # (o job em curso demora) force a saida pelo caminho normal do processo.
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    previous = {sig: signal.signal(sig, request_stop) for sig in handled}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def cmd_worker(args: argparse.Namespace) -> int:
    """Processa a fila de geracao de texto ate `SIGINT`/`SIGTERM` (o job em curso termina)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    stop = threading.Event()
    with open_database(settings) as session_factory:
        worker = build_worker(settings, session_factory)
        with stop_on_signals(stop):
            print(
                "worker a escutar a fila (Ctrl+C ou SIGTERM para parar)",
                file=sys.stderr,
                flush=True,
            )
            try:
                worker.run_forever(stop)
            except KeyboardInterrupt:  # 2o Ctrl+C: o tratador por omissao interrompe o job em curso
                print(
                    "worker interrompido (2o sinal); o job em curso recupera-se no arranque",
                    file=sys.stderr,
                )
                return EXIT_INTERRUPTED
    print("worker terminado", file=sys.stderr)
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
    "worker": cmd_worker,
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
