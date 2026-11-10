"""Pecas de arranque partilhadas pelo CLI e pelo painel: definicoes, base e orcamento.

Os erros esperados de configuracao sao `StartupError`: a mensagem nomeia a variavel em causa e
nunca o seu valor (chaves de API, segredos e a palavra-passe da base nunca saem daqui).
"""

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

from pydantic import BaseModel, ValidationError
from pydantic_settings import SettingsError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from marketing_agent.agents.diretor import DIRETOR_DIR
from marketing_agent.agents.diretor.schemas import (
    Diagnostico,
    Estrategia,
    InterviewTurnOutput,
    PerfilMarca,
)
from marketing_agent.budget.controller import BudgetController
from marketing_agent.budget.pricing import PriceBook
from marketing_agent.clock import Clock
from marketing_agent.config import Settings
from marketing_agent.content.specs import ContentSpecs, load_content_specs
from marketing_agent.db.base import make_engine, make_session_factory
from marketing_agent.harness.spec import SpecLoadError, load_agent_spec
from marketing_agent.llm.types import LLM_CALL_TIMEOUT_S
from marketing_agent.planning.schemas import PlanoSemanal

logger = logging.getLogger(__name__)

_DIRECTOR_SCHEMAS: Mapping[str, type[BaseModel]] = {
    "InterviewTurnOutput": InterviewTurnOutput,
    "PerfilMarca": PerfilMarca,
    "Diagnostico": Diagnostico,
    "Estrategia": Estrategia,
    "PlanoSemanal": PlanoSemanal,
}


_FIELD_IN_SETTINGS_ERROR = re.compile(r'field "(\w+)"')


class StartupError(RuntimeError):
    """Configuracao ou ambiente invalido no arranque; a mensagem e curta e segura de mostrar."""


def load_settings() -> Settings:
    """Le as definicoes do ambiente e do `.env`, traduzindo os erros para `StartupError`.

    A mensagem lista so os nomes das variaveis e o tipo do erro (ex.: `missing`,
    `json_invalid`); os valores nunca sao repetidos, pois podem ser segredos.
    """
    try:
        return Settings()
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        problems = sorted({_describe(error["loc"], error["type"]) for error in errors})
        raise StartupError("configuracao invalida: " + "; ".join(problems)) from None
    except SettingsError as exc:
        # So o nome do campo sai da mensagem; a causa (que pode trazer o valor) nao e propagada.
        match = _FIELD_IN_SETTINGS_ERROR.search(str(exc))
        variable = match.group(1).upper() if match else "?"
        raise StartupError(f"configuracao invalida: {variable} (valor ilegivel)") from None


def _describe(location: tuple[int | str, ...], error_type: str) -> str:
    variable = str(location[0]).upper() if location else "?"
    return f"{variable} ({error_type})"


@contextmanager
def open_database(settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Fabrica de sessoes sobre a base das definicoes; o motor e libertado ao sair."""
    engine = make_engine(settings.database_url)
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()


def build_budget_controller(
    settings: Settings, session_factory: sessionmaker[Session], clock: Clock
) -> BudgetController:
    """Controlador de orcamento com a referencia e os avisos das definicoes."""
    return BudgetController(
        session_factory,
        PriceBook(session_factory),
        clock,
        Decimal(settings.monthly_budget_reference_usd),
        settings.budget_warn_percents,
    )


def director_max_duration_s() -> int:
    """Maior `max_duration_s` entre os specs do Diretor (limiar de recuperacao no arranque)."""
    paths = sorted(DIRETOR_DIR.glob("spec_*.yaml"))
    if not paths:
        raise StartupError(f"nenhum spec do Diretor encontrado em {DIRETOR_DIR}")
    try:
        specs = [load_agent_spec(path, _DIRECTOR_SCHEMAS) for path in paths]
    except SpecLoadError as exc:
        raise StartupError(f"spec do Diretor invalido: {exc}") from exc
    return max(spec.limits.max_duration_s for spec in specs)


def _content_specs() -> ContentSpecs:
    try:
        return load_content_specs()
    except SpecLoadError as exc:
        raise StartupError(f"spec do Copywriter ou do Revisor invalido: {exc}") from exc


def _call_timeout() -> timedelta:
    return timedelta(seconds=LLM_CALL_TIMEOUT_S)


def content_job_lease() -> timedelta:
    """Lease de um job `generate_post`: 3 x (Copywriter + Revisor + 2 chamadas) + 60 s.

    Um job faz ate 3 passagens (1 + 2 voltas), cada uma com um Copywriter e um Revisor. O motor
    so verifica `max_duration_s` ANTES de cada passo: uma execucao que inicia a chamada perto do
    limite pode correr mais `LLM_CALL_TIMEOUT_S`. Por isso cada papel soma uma chamada em
    excesso (2 por passagem); os 60 s finais cobrem as esperas de rede e a gravacao. Com uma
    lease mais curta outro worker reclamaria um job ainda vivo.
    """
    specs = _content_specs()
    run_limits = specs.copywriter.limits.max_duration_s + specs.reviewer.limits.max_duration_s
    per_pass = timedelta(seconds=run_limits) + 2 * _call_timeout()
    return 3 * per_pass + timedelta(seconds=60)


def worker_stale_after() -> timedelta:
    """Idade a partir da qual uma reserva aberta e de um processo morto, vista pelo worker.

    A execucao mais longa possivel de QUALQUER papel (o painel partilha a base com o worker):
    o maior `max_duration_s` mais uma chamada em excesso (`LLM_CALL_TIMEOUT_S`). Uma reserva mais
    nova que isto pode ser de uma execucao viva do outro processo.
    """
    specs = _content_specs()
    longest = max(
        director_max_duration_s(),
        specs.copywriter.limits.max_duration_s,
        specs.reviewer.limits.max_duration_s,
    )
    return timedelta(seconds=longest) + _call_timeout()


def database_error_message(exc: SQLAlchemyError) -> str:
    """Mensagem curta sobre a base, sem a URL: o driver nao repete a palavra-passe.

    So a primeira linha da causa do driver sai; a cadeia original nao deve ser propagada.
    """
    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    detail = lines[0] if lines else type(cause).__name__
    return (
        f"base de dados (DATABASE_URL) inacessivel ou sem migracoes ({detail}); "
        "execute `marketing-agent migrate` e confirme o servidor"
    )
