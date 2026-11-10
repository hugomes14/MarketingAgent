"""Teste de fumo do texto semanal contra modelos locais servidos pelo Ollama.

Percurso com os servicos reais, numa base descartavel: entrevista (ate 6 respostas), perfil,
diagnostico e estrategia; um item de preco na base de conhecimento; o plano semanal do Diretor
(`--posts` publicacoes), aprovado; o worker a esvaziar a fila uma vez (Copywriter e Revisor); e o
lote da semana. No fim imprime o tempo de cada passo, as voltas do Revisor por publicacao e as
saidas invalidas por papel. Sem custos: os modelos locais tem preco 0.

SEGURANCA. Nunca toca na base de desenvolvimento nem nas portas do painel: por omissao cria uma
base `marketing_agent_smoke_<aleatorio>` no Postgres de teste (127.0.0.1:5433), migra-a, usa-a e
apaga-a no fim (mesmo se falhar). Com `--database-url` usa uma base ja existente (nunca a apaga) e
recusa qualquer URL que aponte a porta 5434, a base `marketing_agent` (desenvolvimento), a base
`marketing_agent_test` (partilhada pelo pytest) ou a um servidor fora do loopback. Nunca descarrega
modelos: se o Ollama estiver em baixo ou faltar um modelo, termina com uma mensagem fixa.

Requisitos: Ollama em `--ollama-url` com os modelos pedidos (`ollama list`); Postgres de
`compose.test.yaml` a correr. Escreve um ficheiro de chave descartavel num diretorio temporario.
Codigos de saida: 0 tudo correu e as publicacoes ficaram `in_review`; 1 um passo ou uma
publicacao falhou; 2 argumentos invalidos; 3 Ollama inacessivel ou modelo em falta.

Uso, a partir da raiz do repositorio:
    uv run python scripts/smoke_ollama.py --posts 3
    uv run python scripts/smoke_ollama.py --database-url postgresql+psycopg://u:p@127.0.0.1:5433/x
"""

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.artifacts.store import ArtifactRecord, ArtifactStore
from marketing_agent.budget.pricing import ModelPrice, PriceTier, load_model_prices
from marketing_agent.budget.pricing import sync_model_prices as store_model_prices
from marketing_agent.cli import build_worker
from marketing_agent.clock import SystemClock
from marketing_agent.config import RoleRoute, Settings
from marketing_agent.db.models import AgentRun, AgentStep, Brand, CostEntry, Post, PostRevision
from marketing_agent.harness.engine import AgentEngine, RunResult, RunStatus
from marketing_agent.interview.service import InterviewService
from marketing_agent.kb.service import NewKbEntry
from marketing_agent.llm.factory import build_llm_router
from marketing_agent.panel.assembly import build_content_services
from marketing_agent.panel.queries import PanelQueries
from marketing_agent.startup import build_budget_controller, open_database
from marketing_agent.strategy.service import StrategyService

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_URL = "postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5433/postgres"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
LOCAL_PRICES = REPO / "config" / "model_prices.local.yaml"
ACTOR = "smoke@local"
DEV_DATABASE_PORT = 5434
PROTECTED_DATABASES = frozenset({"marketing_agent", "marketing_agent_test"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
THROWAWAY_PREFIX = "marketing_agent_smoke_"
_THROWAWAY_PATTERN = re.compile(rf"{THROWAWAY_PREFIX}[0-9a-f]{{8}}")
MAX_JOBS = 50  # trava de seguranca do esvaziar da fila
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_OLLAMA = 0, 1, 2, 3
OLLAMA_DOWN_MESSAGE = (
    "Ollama inacessivel em {url}. Arranque o Ollama; este script nunca o arranca nem o reinicia."
)
MODELS_MISSING_MESSAGE = (
    "Modelos em falta no Ollama: {models}. Este script nunca descarrega modelos: "
    "use `ollama pull <modelo>` e volte a correr."
)

ANSWERS = [
    "Somos uma padaria artesanal em Braga. Vendemos pao de fermentacao natural, "
    "bolos por encomenda e cafe.",
    "Os clientes principais sao familias do bairro e pequenos escritorios que "
    "encomendam lanches para reunioes.",
    "Servimos Braga e Guimaraes. Entregamos ao domicilio so em Braga, num raio de 5 km. "
    "Diferenciamo-nos por usar farinhas locais e fermento proprio com mais de 10 anos.",
    "Hoje os clientes chegam sobretudo por passa-palavra e pelo Instagram. Perdemos "
    "encomendas grandes porque respondemos tarde as mensagens.",
    "Fazemos ate 30 encomendas por semana com a equipa atual de 4 pessoas. Temos "
    "fotografias profissionais dos produtos e avaliacoes no Google com 4,8 estrelas.",
    "O tom da marca e caloroso e simples. Concorrentes: duas pastelarias grandes da zona. "
    "Objetivo: duplicar as encomendas de empresas em 6 meses.",
]


class SmokeError(Exception):
    """Falha esperada do teste de fumo; a mensagem e curta e segura de imprimir."""


class UnsafeDatabase(SmokeError):
    """O URL aponta a uma base que este script nunca deve tocar."""


class OllamaUnavailable(SmokeError):
    """O Ollama nao responde ou falta um modelo; a mensagem ja e a final."""


@dataclass(frozen=True)
class SmokeArgs:
    """Argumentos validados da linha de comandos."""

    database_url: str | None
    server_url: str
    ollama_url: str
    model: str
    reviewer_model: str
    posts: int


@dataclass(frozen=True)
class StepTiming:
    """Duracao e resultado de um passo do percurso."""

    name: str
    seconds: float
    is_ok: bool
    detail: str = ""


@dataclass(frozen=True)
class PostOutcome:
    """Estado final de uma publicacao do lote."""

    slot_key: str
    lifecycle: str
    review_rounds: int
    verdict: str | None
    last_error: str | None


@dataclass
class SmokeReport:
    """Tudo o que o relatorio final mostra."""

    week: str = ""
    steps: list[StepTiming] = field(default_factory=list)
    posts: list[PostOutcome] = field(default_factory=list)
    invalid_outputs: dict[str, dict[str, int]] = field(default_factory=dict)
    runs: dict[str, int] = field(default_factory=dict)
    cost_entries: int = 0

    @property
    def is_success(self) -> bool:
        """Todos os passos correram e todas as publicacoes chegaram a `in_review`."""
        steps_ok = all(step.is_ok for step in self.steps)
        return steps_ok and bool(self.posts) and all(p.lifecycle == "in_review" for p in self.posts)


# ---------------------------------------------------------------- argumentos e seguranca


def parse_args(argv: Sequence[str] | None = None) -> SmokeArgs:
    """Le e valida os argumentos; erro de uso termina com o codigo 2 (via `argparse`)."""
    parser = argparse.ArgumentParser(
        prog="smoke_ollama.py",
        description=(__doc__ or "").split("\n\n")[0],
        epilog="Ver o topo de scripts/smoke_ollama.py para a seguranca e os codigos de saida.",
    )
    parser.add_argument(
        "--database-url",
        help="base existente a usar (nunca e apagada); recusa a base de desenvolvimento "
        f"(porta {DEV_DATABASE_PORT} ou base marketing_agent), a base do pytest e hosts fora do "
        "loopback. Por omissao cria e apaga uma base descartavel no Postgres de teste.",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help="servidor onde criar a base descartavel (por omissao o Postgres de teste, "
        f"porta 5433; a porta {DEV_DATABASE_PORT} e recusada)",
    )
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="endereco do Ollama")
    parser.add_argument(
        "--model",
        default=os.environ.get("MODEL", "qwen2.5:14b"),
        help="modelo do Diretor e do Copywriter (por omissao a variavel MODEL ou qwen2.5:14b)",
    )
    parser.add_argument(
        "--reviewer-model", default="llama3.1:8b", help="modelo do Revisor (distinto do Copywriter)"
    )
    parser.add_argument("--posts", type=int, default=3, help="publicacoes do plano (1 a 14)")
    parsed = parser.parse_args(argv)
    if not 1 <= parsed.posts <= 14:
        parser.error("--posts tem de estar entre 1 e 14")
    try:
        check_server_url(parsed.server_url)
        if parsed.database_url:
            check_database_url(parsed.database_url)
    except UnsafeDatabase as exc:
        parser.error(str(exc))
    return SmokeArgs(
        parsed.database_url,
        parsed.server_url,
        parsed.ollama_url.rstrip("/"),
        parsed.model,
        parsed.reviewer_model,
        parsed.posts,
    )


def _parse_url(raw: str) -> URL:
    try:
        return make_url(raw)
    except ArgumentError:
        # Nunca repetir `raw`: pode trazer a palavra-passe.
        raise UnsafeDatabase("URL de base de dados ilegivel") from None


def check_server_url(raw: str) -> URL:
    """Servidor onde criar bases descartaveis: loopback e nunca a porta de desenvolvimento."""
    url = _parse_url(raw)
    if url.host not in LOOPBACK_HOSTS:
        raise UnsafeDatabase("recusado: o servidor Postgres tem de estar em loopback")
    if url.port == DEV_DATABASE_PORT:
        raise UnsafeDatabase(f"recusado: a porta {DEV_DATABASE_PORT} e a base de desenvolvimento")
    return url


def check_database_url(raw: str) -> URL:
    """Base a usar tal como esta: servidor seguro e base que nao e a de desenvolvimento/pytest."""
    url = check_server_url(raw)
    if not url.database:
        raise UnsafeDatabase("recusado: o URL nao indica a base de dados")
    if url.database in PROTECTED_DATABASES:
        raise UnsafeDatabase(f"recusado: a base {url.database!r} nao pode ser usada no teste")
    return url


def new_throwaway_name() -> str:
    """Nome aleatorio de base descartavel (so hexadecimal, seguro como identificador SQL)."""
    return f"{THROWAWAY_PREFIX}{secrets.token_hex(4)}"


@contextmanager
def throwaway_database(server_url: str) -> Iterator[str]:
    """Cria uma base descartavel no servidor, devolve o seu URL e apaga-a no fim."""
    server = check_server_url(server_url)
    name = new_throwaway_name()
    assert _THROWAWAY_PATTERN.fullmatch(name)  # o nome vai direto para SQL
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        try:
            yield server.set(database=name).render_as_string(hide_password=False)
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        admin.dispose()


# ---------------------------------------------------------------- Ollama


def missing_models(available: Collection[str], required: Collection[str]) -> list[str]:
    """Modelos pedidos que o Ollama nao tem (comparacao por nome exato, ordem estavel)."""
    return sorted({model for model in required if model not in available})


def list_ollama_models(base_url: str) -> set[str]:
    """Nomes dos modelos instalados; `OllamaUnavailable` se o Ollama nao responde."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            body = json.load(response)
        return {str(model["name"]) for model in body["models"]}
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        raise OllamaUnavailable(OLLAMA_DOWN_MESSAGE.format(url=base_url)) from None


def require_models(base_url: str, required: Collection[str]) -> None:
    """Falha com mensagem fixa se o Ollama esta em baixo ou falta algum modelo; nunca descarrega."""
    missing = missing_models(list_ollama_models(base_url), required)
    if missing:
        raise OllamaUnavailable(MODELS_MISSING_MESSAGE.format(models=", ".join(missing)))


# ---------------------------------------------------------------- relatorio


def format_report(report: SmokeReport) -> str:
    """Texto do relatorio final: passos com tempo, voltas do Revisor e saidas invalidas."""
    lines = ["", "=== Relatorio do teste de fumo", f"Semana do plano: {report.week or '-'}", ""]
    lines.append(f"{'passo':<34}{'tempo (s)':>10}  resultado")
    for step in report.steps:
        status = "ok" if step.is_ok else "FALHOU"
        suffix = f" ({step.detail})" if step.detail else ""
        lines.append(f"{step.name:<34}{step.seconds:>10.1f}  {status}{suffix}")
    total = sum(step.seconds for step in report.steps)
    lines.append(f"{'total':<34}{total:>10.1f}")
    lines += ["", f"Publicacoes do lote: {len(report.posts)}"]
    for post in report.posts:
        error = f"; erro={post.last_error}" if post.last_error else ""
        lines.append(
            f"  {post.slot_key}: {post.lifecycle}, voltas do revisor={post.review_rounds}, "
            f"parecer={post.verdict or '-'}{error}"
        )
    lines.append(f"Voltas do revisor (total): {sum(p.review_rounds for p in report.posts)}")
    lines += ["", "Saidas invalidas por papel:"]
    if not any(report.invalid_outputs.values()):
        lines.append("  nenhuma")
    for role, classes in sorted(report.invalid_outputs.items()):
        if classes:
            detail = ", ".join(f"{name}={count}" for name, count in sorted(classes.items()))
            lines.append(f"  {role}: {detail}")
    runs = ", ".join(f"{key}={count}" for key, count in sorted(report.runs.items()))
    lines += ["", f"Execucoes: {runs or '-'}", f"cost_entry: {report.cost_entries}"]
    lines.append("RESULTADO: " + ("OK" if report.is_success else "FALHOU"))
    return "\n".join(lines)


# ---------------------------------------------------------------- percurso


@dataclass
class Note:
    """Detalhe opcional que o corpo de um passo cronometrado deixa para o relatorio."""

    detail: str = ""


@contextmanager
def timed(report: SmokeReport, name: str) -> Iterator[Note]:
    """Cronometra um passo; regista-o no relatorio (como falhado se o corpo levantar)."""
    note = Note()
    started = time.monotonic()
    print(f"\n=== {name}", flush=True)
    try:
        yield note
    except BaseException:
        report.steps.append(StepTiming(name, time.monotonic() - started, False, note.detail))
        raise
    elapsed = time.monotonic() - started
    report.steps.append(StepTiming(name, elapsed, True, note.detail))
    print(f"[{elapsed:5.1f}s] {name}: {note.detail or 'ok'}", flush=True)


def build_settings(args: SmokeArgs, database_url: str, key_file: Path) -> Settings:
    """Definicoes do teste: tres papeis no Ollama, sem ler o `.env` do desenvolvimento."""

    def route(model: str) -> RoleRoute:
        return RoleRoute(
            provider="openai", model_id=model, capabilities=frozenset({"text", "json_schema"})
        )

    return Settings(
        _env_file=None,
        database_url=database_url,
        openai_api_key_file=key_file,
        openai_base_url=f"{args.ollama_url}/v1",
        role_routes={
            "diretor": route(args.model),
            "copywriter": route(args.model),
            "revisor": route(args.reviewer_model),
        },
        plan_max_posts_per_week=args.posts,
        plan_max_posts_per_day=1,
    )


def local_prices(models: Collection[str]) -> list[ModelPrice]:
    """Precos do ficheiro local; um modelo fora dele recebe preco 0 (e local)."""
    prices = load_model_prices(LOCAL_PRICES)
    known = {price.model_id for price in prices if price.provider == "openai"}
    free = (PriceTier(0, Decimal(0), Decimal(0)),)
    for model in sorted(set(models) - known):
        prices.append(ModelPrice("openai", model, "smoke", free, valid_from=prices[0].valid_from))
    return prices


def migrate(database_url: str) -> None:
    """Migra a base ate `head`; o `env.py` do Alembic le `DATABASE_URL` das definicoes."""
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "migrations"))
    command.upgrade(config, "head")


def require_success(result: RunResult, what: str) -> ArtifactRecord:
    """Artefacto produzido por uma execucao bem-sucedida; senao `SmokeError` com o estado."""
    print(f"           estado={result.status} mensagem={result.message}", flush=True)
    if result.status is not RunStatus.SUCCEEDED or not isinstance(result.output, ArtifactRecord):
        raise SmokeError(f"{what}: execucao {result.status.value}")
    return result.output


@dataclass(frozen=True)
class Services:
    """Servicos reais montados sobre a base descartavel."""

    settings: Settings
    session_factory: sessionmaker[Session]
    store: ArtifactStore
    interview: InterviewService
    strategy: StrategyService
    engine: AgentEngine
    clock: SystemClock


def build_services(settings: Settings, session_factory: sessionmaker[Session]) -> Services:
    """Motor, loja e servicos do Diretor sobre o router das definicoes."""
    clock = SystemClock()
    budget = build_budget_controller(settings, session_factory, clock)
    store = ArtifactStore(session_factory, clock)
    engine = AgentEngine(session_factory, build_llm_router(settings), budget, store, clock)
    interview = InterviewService(session_factory, engine, store, settings, clock)
    strategy = StrategyService(session_factory, engine, store, clock)
    return Services(settings, session_factory, store, interview, strategy, engine, clock)


def run_interview(report: SmokeReport, services: Services, brand_id: str) -> None:
    """Entrevista, perfil e diagnostico."""
    interview, strategy = services.interview, services.strategy
    with timed(report, "entrevista") as note:
        started = interview.start(brand_id)
        session_id = started.session_id
        for index, answer in enumerate(ANSWERS, 1):
            result = interview.submit_answer(session_id, answer, f"smoke-{session_id}-{index}")
            print(f"           resposta {index}: estado={result.status.value}", flush=True)
            if result.status.value != "completed" or result.ready_for_profile:
                break
        note.detail = f"{index} respostas"
    with timed(report, "perfil"):
        profile_id = interview.request_profile(session_id)
        interview.confirm_profile(profile_id, ACTOR)
    with timed(report, "diagnostico"):
        diagnosis = require_success(strategy.propose_diagnosis(brand_id), "diagnostico")
        strategy.approve_diagnosis(diagnosis.id, ACTOR)


def run_strategy(report: SmokeReport, services: Services, brand_id: str) -> None:
    """Estrategia: aceita a primeira proposta de oferta e recusa as restantes."""
    with timed(report, "estrategia") as note:
        proposed = require_success(services.strategy.propose_strategy(brand_id), "estrategia")
        proposals = services.store.get(proposed.id).payload["propostas_oferta"]
        count = len(proposals) if isinstance(proposals, list) else 0
        decisions = {i: i == 0 for i in range(count)}
        services.strategy.approve_strategy(proposed.id, ACTOR, decisions)
        note.detail = f"{count} propostas"


def run_worker(report: SmokeReport, services: Services) -> None:
    """Esvazia a fila uma vez, com o worker real (`build_worker`), cronometrando cada job."""
    worker = build_worker(services.settings, services.session_factory)
    print("\n=== worker", flush=True)
    for number in range(1, MAX_JOBS + 1):
        started = time.monotonic()
        if not worker.run_once():  # um job que falha fica no post (`last_error`), nao aqui
            print("           fila vazia", flush=True)
            return
        elapsed = time.monotonic() - started
        report.steps.append(StepTiming(f"worker: job {number}", elapsed, True))
        print(f"[{elapsed:5.1f}s] job {number}", flush=True)
    raise SmokeError(f"a fila nao esvaziou em {MAX_JOBS} jobs")


def run_weekly_text(report: SmokeReport, services: Services, brand_id: str) -> None:
    """Base de conhecimento, plano, worker (uma passagem pela fila) e lote."""
    content = build_content_services(
        services.settings,
        services.session_factory,
        services.engine,
        services.store,
        services.strategy,
        services.clock,
    )
    with timed(report, "base de conhecimento"):
        today = services.clock.now().date()
        item = NewKbEntry(
            kind="preco",
            key="pao-de-forno",
            title="Pao de forno a lenha",
            body="Pao de forno a lenha, cozido todas as manhas.",
            price_amount=Decimal("4.50"),
            price_currency="EUR",
            valid_from=today,
            valid_until=today + timedelta(days=60),
            source_kind="dito_pelo_responsavel",
        )
        content.kb.add(brand_id, item, ACTOR)
    with timed(report, "plano semanal") as note:
        plan = require_success(content.planning.propose_plan(brand_id), "plano")
        report.week = str(services.store.get(plan.id).payload["semana"])
        post_ids = content.planning.approve_plan(plan.id, ACTOR)
        note.detail = f"{len(post_ids)} publicacoes na semana {report.week}"
    run_worker(report, services)
    with timed(report, "lote") as note:
        queries = PanelQueries(services.session_factory, services.store)
        view = queries.batch_view(brand_id, report.week, content.kb.valid_refs)
        note.detail = ", ".join(f"{n} {state}" for state, n in view.counts)


def collect_statistics(session_factory: sessionmaker[Session], report: SmokeReport) -> None:
    """Saidas invalidas por papel, execucoes por papel/estado e linhas de custo."""
    with session_factory() as session:
        invalid = session.execute(
            select(AgentRun.role, AgentStep.error_class, func.count())
            .join(AgentRun, AgentRun.id == AgentStep.run_id)
            .where(AgentStep.error_class.is_not(None))
            .group_by(AgentRun.role, AgentStep.error_class)
        )
        for role, error_class, count in invalid:
            report.invalid_outputs.setdefault(role, {})[str(error_class)] = count
        runs = session.execute(
            select(AgentRun.role, AgentRun.status, func.count()).group_by(
                AgentRun.role, AgentRun.status
            )
        )
        for role, status, count in runs:
            report.runs[f"{role}/{status}"] = count
        report.cost_entries = session.scalar(select(func.count()).select_from(CostEntry)) or 0


def post_outcomes(session_factory: sessionmaker[Session], week: str) -> list[PostOutcome]:
    """Estado, voltas do Revisor e ultimo parecer de cada publicacao da semana."""
    outcomes = []
    with session_factory() as session:
        for post in session.scalars(select(Post).order_by(Post.slot_key)):
            if not post.slot_key.startswith(week):
                continue
            verdict = session.scalar(
                select(PostRevision.review_verdict)
                .where(PostRevision.post_id == post.id)
                .order_by(PostRevision.rev_no.desc())
                .limit(1)
            )
            outcomes.append(
                PostOutcome(
                    post.slot_key, post.lifecycle, post.review_rounds, verdict, post.last_error
                )
            )
    return outcomes


def _new_brand(session_factory: sessionmaker[Session]) -> str:
    with session_factory.begin() as session:
        brand = Brand(name=f"Padaria teste {int(time.time())}")
        session.add(brand)
        session.flush()
        return brand.id


def run_smoke(args: SmokeArgs, database_url: str, report: SmokeReport) -> None:
    """Percurso completo sobre `database_url` (ja verificada e vazia)."""
    migrate(database_url)
    with tempfile.TemporaryDirectory() as directory:
        key_file = Path(directory) / "ollama_key"
        key_file.write_text("local\n", encoding="utf-8")
        settings = build_settings(args, database_url, key_file)
        with open_database(settings) as session_factory:
            store_model_prices(session_factory, local_prices({args.model, args.reviewer_model}))
            services = build_services(settings, session_factory)
            try:
                brand_id = _new_brand(session_factory)
                run_interview(report, services, brand_id)
                run_strategy(report, services, brand_id)
                run_weekly_text(report, services, brand_id)
            finally:
                collect_statistics(session_factory, report)
                report.posts = post_outcomes(session_factory, report.week)


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada: valida, verifica o Ollama, corre o percurso e imprime o relatorio."""
    args = parse_args(argv)
    try:
        require_models(args.ollama_url, {args.model, args.reviewer_model})
    except OllamaUnavailable as exc:
        print(exc, file=sys.stderr)
        return EXIT_OLLAMA
    report = SmokeReport()
    try:
        if args.database_url:
            run_smoke(args, args.database_url, report)
        else:
            with throwaway_database(args.server_url) as database_url:
                run_smoke(args, database_url, report)
    except SmokeError as exc:
        print(f"\nFALHOU: {exc}", file=sys.stderr)
    except Exception as exc:  # um passo a falhar nao pode esconder o que ja foi medido
        print(f"\nFALHOU: {type(exc).__name__}", file=sys.stderr)
    print(format_report(report))
    return EXIT_OK if report.is_success else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
