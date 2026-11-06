"""Teste de fumo do nucleo (Diretor) contra um modelo local servido pelo Ollama.

Percurso completo com o servico real: entrevista (ate 6 respostas), perfil, diagnostico e
estrategia (uma proposta aceite, as restantes recusadas). Sem custos: o preco do modelo local e 0.

Requisitos: Ollama em http://127.0.0.1:11434 com o modelo pedido; Postgres de `compose.test.yaml`
com uma base `marketing_smoke` (CREATE DATABASE marketing_smoke); corre as migracoes sozinho.
Entradas: variavel MODEL (por omissao qwen2.5:14b). Saidas: estado de cada passo e um resumo das
execucoes; escreve um ficheiro de chave descartavel ("local") ao lado do script.
Efeitos: cria uma marca nova e linhas na base marketing_smoke (descartavel).
Uso, a partir da raiz do repositorio:
    MODEL=qwen2.5:14b uv run python scripts/smoke_ollama.py
"""

import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

REPO = Path.cwd()
SCRATCH = Path(tempfile.gettempdir())
MODEL = os.environ.get("MODEL", "qwen2.5:14b")
DB = "postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5433/marketing_smoke"
KEY_FILE = SCRATCH / "marketing_agent_ollama_key"
KEY_FILE.write_text("local\n", encoding="utf-8")
os.environ["DATABASE_URL"] = DB

from marketing_agent.artifacts.store import ArtifactStore, ArtifactType  # noqa: E402
from marketing_agent.budget.controller import BudgetController  # noqa: E402
from marketing_agent.budget.pricing import (  # noqa: E402
    ModelPrice,
    PriceBook,
    PriceTier,
    sync_model_prices,
)
from marketing_agent.clock import SystemClock  # noqa: E402
from marketing_agent.config import RoleRoute, Settings  # noqa: E402
from marketing_agent.db.base import make_engine, make_session_factory  # noqa: E402
from marketing_agent.db.models import AgentRun, AgentStep, Brand, CostEntry  # noqa: E402
from marketing_agent.harness.engine import AgentEngine  # noqa: E402
from marketing_agent.interview.service import InterviewService  # noqa: E402
from marketing_agent.llm.factory import build_llm_router  # noqa: E402
from marketing_agent.strategy.service import StrategyService  # noqa: E402

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


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def main() -> int:
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    command.upgrade(cfg, "head")

    settings = Settings(
        database_url=DB,
        openai_api_key_file=KEY_FILE,
        openai_base_url="http://127.0.0.1:11434/v1",
        role_routes={
            "diretor": RoleRoute(
                provider="openai",
                model_id=MODEL,
                capabilities=frozenset({"text", "json_schema"}),
            )
        },
    )
    sf = make_session_factory(make_engine(DB))
    clock = SystemClock()
    sync_model_prices(
        sf,
        [
            ModelPrice(
                "openai",
                MODEL,
                "smoke",
                (PriceTier(0, Decimal(0), Decimal(0)),),
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )
    router = build_llm_router(settings)
    budget = BudgetController(sf, PriceBook(sf), clock, Decimal("20"), (50, 80, 100))
    store = ArtifactStore(sf, clock)
    engine = AgentEngine(sf, router, budget, store, clock)
    interview = InterviewService(sf, engine, store, settings, clock)
    strategy = StrategyService(sf, engine, store, clock)

    with sf.begin() as session:
        brand = Brand(name=f"Padaria teste {int(time.time())}")
        session.add(brand)
        session.flush()
        brand_id = brand.id

    step(f"entrevista (modelo {MODEL})")
    t0 = time.time()
    started = interview.start(brand_id)
    print(f"[{time.time() - t0:5.1f}s] 1a pergunta: {started.question}")
    session_id = started.session_id
    for i, answer in enumerate(ANSWERS, 1):
        t0 = time.time()
        result = interview.submit_answer(session_id, answer, f"smoke-{session_id}-{i}")
        print(
            f"[{time.time() - t0:5.1f}s] resposta {i}: status={result.status} "
            f"pronto={result.ready_for_profile} injecao={result.injection_suspected}"
        )
        print(f"           pergunta: {result.question}\n           resumo: {result.summary[:140]}")
        if result.message:
            print(f"           mensagem: {result.message}")
        if result.status.value != "completed" or result.ready_for_profile:
            break
    known = sum(1 for g in result.gap_map if g.status != "desconhecido")
    print(f"topicos com informacao: {known}/{len(result.gap_map)}")

    step("perfil")
    t0 = time.time()
    profile_id = interview.request_profile(session_id)
    print(f"[{time.time() - t0:5.1f}s] perfil submetido: {profile_id}")
    interview.confirm_profile(profile_id, "smoke@local")

    step("diagnostico")
    t0 = time.time()
    run = strategy.propose_diagnosis(brand_id)
    print(f"[{time.time() - t0:5.1f}s] estado={run.status} mensagem={run.message}")
    if run.status.value != "succeeded":
        return report(sf, 1)
    diag_id = run.output.id  # type: ignore[union-attr]
    strategy.approve_diagnosis(diag_id, "smoke@local")

    step("estrategia")
    t0 = time.time()
    run = strategy.propose_strategy(brand_id)
    print(f"[{time.time() - t0:5.1f}s] estado={run.status} mensagem={run.message}")
    if run.status.value != "succeeded":
        return report(sf, 1)
    strat_id = run.output.id  # type: ignore[union-attr]
    payload = store.get(strat_id).payload
    proposals = payload["propostas_oferta"]
    print(f"{len(proposals)} propostas de oferta")  # type: ignore[arg-type]
    decisions = {i: i == 0 for i in range(len(proposals))}  # type: ignore[arg-type]
    strategy.approve_strategy(strat_id, "smoke@local", decisions)
    active = strategy.active_strategy(brand_id)
    print(f"propostas aceites na vista ativa: {len(active.propostas_aceites) if active else 0}")
    active_profile = store.active(brand_id, ArtifactType.PERFIL_MARCA)
    print(f"perfil ativo: {bool(active_profile)}")
    return report(sf, 0)


def report(sf, code: int) -> int:  # type: ignore[no-untyped-def]
    step("resumo de execucoes")
    with sf() as session:
        for status, n in session.execute(
            select(AgentRun.status, func.count()).group_by(AgentRun.status)
        ):
            print(f"agent_run {status}: {n}")
        for err, n in session.execute(
            select(AgentStep.error_class, func.count()).group_by(AgentStep.error_class)
        ):
            print(f"agent_step error_class={err}: {n}")
        print("cost_entry:", session.scalar(select(func.count()).select_from(CostEntry)))
        print(
            "reservas por estado:",
            list(session.execute(text("select status, count(*) from cost_reservation group by 1"))),
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
