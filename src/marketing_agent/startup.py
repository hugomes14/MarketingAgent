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
from marketing_agent.budget.pricing import (
    ImageUsage,
    ModelPrice,
    PriceBook,
    UnknownPrice,
    compute_image_cost_usd,
)
from marketing_agent.clock import Clock
from marketing_agent.config import IMAGE_ROUTE, RoleRoute, Settings
from marketing_agent.content.design import GENERATED_SIZE
from marketing_agent.content.specs import ContentSpecs, load_content_specs
from marketing_agent.db.base import make_engine, make_session_factory
from marketing_agent.harness.engine import NETWORK_BACKOFF_S, run_overrun_bound_s
from marketing_agent.harness.image_calls import MAX_IMAGES_PER_REQUEST
from marketing_agent.harness.spec import AgentSpec, SpecLoadError, load_agent_spec
from marketing_agent.images.factory import build_image_backend
from marketing_agent.llm.types import IMAGE_CALL_TIMEOUT_S, LLM_CALL_TIMEOUT_S
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
        raise StartupError(
            f"spec do Copywriter, do Revisor ou do Designer invalido: {exc}"
        ) from exc


_SYNC_PRICES = "correr 'marketing-agent sync-prices'"


def check_content_routes(settings: Settings, prices: PriceBook) -> None:
    """Recusa no arranque rotas que fariam o worker faturar e depois falhar.

    Sem isto, uma rota do Revisor sem `image_input` (ou sem preco) so falha na execucao do
    Revisor, DEPOIS de o Copywriter ter sido faturado, e o job repete-se (3 tentativas). Para
    cada papel de conteudo COM rota, as capacidades do spec tem de estar na rota e o modelo
    tem de ter preco; com rota `designer_imagem`, a chave tem de existir e ter preco por imagem
    para a qualidade configurada e o tamanho gerado. As mensagens so levam nomes (papel,
    fornecedor, modelo, capacidades, variavel), nunca valores de segredos.

    Raises:
        StartupError: na primeira rota que falha, com uma mensagem fixa.
    """
    specs = _content_specs()
    routed = [
        (spec, route)
        for spec in (specs.copywriter, specs.reviewer, specs.designer)
        if (route := settings.role_routes.get(spec.role)) is not None
    ]
    for spec, route in routed:
        _check_capabilities(spec, route)
    for spec, route in routed:
        _check_text_price(spec.role, route, prices)
    _check_image_route(settings, prices)


def _check_capabilities(spec: AgentSpec, route: RoleRoute) -> None:
    missing = sorted(spec.required_capabilities - route.capabilities)
    if missing:
        raise StartupError(
            f"ROLE_ROUTES: a rota '{spec.role}' precisa das capacidades {missing}; "
            "ver .env.local.example"
        )


def _price_or_none(prices: PriceBook, route: RoleRoute) -> ModelPrice | None:
    try:
        return prices.get(route.provider, route.model_id)
    except UnknownPrice:
        return None


def _check_text_price(role: str, route: RoleRoute, prices: PriceBook) -> None:
    if _price_or_none(prices, route) is None:
        raise StartupError(
            f"ROLE_ROUTES: a rota '{role}' ({route.provider}/{route.model_id}) nao tem preco; "
            f"{_SYNC_PRICES}"
        )


def _check_image_route(settings: Settings, prices: PriceBook) -> None:
    route = settings.role_routes.get(IMAGE_ROUTE)
    if route is None:
        return  # sem rota usa-se o gerador falso, de custo zero
    try:
        backend = build_image_backend(settings)
    except ValueError as exc:  # a mensagem nomeia a variavel, nunca o conteudo da chave
        raise StartupError(str(exc)) from exc
    price = _price_or_none(prices, route)
    where = f"a rota '{IMAGE_ROUTE}' ({route.provider}/{route.model_id})"
    if price is None:
        raise StartupError(f"ROLE_ROUTES: {where} nao tem preco; {_SYNC_PRICES}")
    if not _has_image_price(price, GENERATED_SIZE, backend.quality):
        raise StartupError(
            f"ROLE_ROUTES: {where} nao tem preco por imagem para quality={backend.quality}, "
            f"size={GENERATED_SIZE}; {_SYNC_PRICES}"
        )


def _has_image_price(price: ModelPrice, size: str, quality: str) -> bool:
    try:
        compute_image_cost_usd(price, ImageUsage(1, size, quality))
    except UnknownPrice:
        return False
    return True


def _call_timeout(time_multiplier: int = 1) -> timedelta:
    return timedelta(seconds=LLM_CALL_TIMEOUT_S * time_multiplier)


def _image_call_bound() -> timedelta:
    """Pior caso das chamadas de imagem de uma passagem do pacote.

    Cada geracao possivel (`MAX_IMAGES_PER_REQUEST`) pode ser tentada `1 + len(NETWORK_BACKOFF_S)`
    vezes (uma repeticao por falha de rede) e cada tentativa pode durar `IMAGE_CALL_TIMEOUT_S`.
    """
    attempts = MAX_IMAGES_PER_REQUEST * (1 + len(NETWORK_BACKOFF_S))
    return attempts * timedelta(seconds=IMAGE_CALL_TIMEOUT_S)


def content_job_lease(time_multiplier: int = 1) -> timedelta:
    """Lease de um job `generate_post`: 3 x (Copywriter + Designer + Revisor + chamadas) + 60 s.

    `time_multiplier` escala os limites dos specs e chamadas de texto; imagem nao escala.
    Um job faz ate 3 passagens (1 + 2 voltas), cada uma com Copywriter, Designer e Revisor. O
    motor so verifica `max_duration_s` ANTES de cada passo: uma execucao que inicia a chamada
    perto do limite pode correr mais `LLM_CALL_TIMEOUT_S`, por isso cada um dos tres papeis soma
    uma chamada em excesso. A isso soma-se, por passagem, o pior caso das chamadas de imagem
    (`_image_call_bound`: `IMAGE_CALL_TIMEOUT_S` por tentativa, com as repeticoes de rede); e um
    limite superior folgado de proposito, porque so custa tempo de recuperacao depois de um
    worker morto. Os 60 s finais cobrem esperas de rede e a gravacao. Com uma lease mais curta
    outro worker reclamaria um job ainda vivo.
    """
    specs = _content_specs()
    run_limits = sum(
        spec.limits.max_duration_s for spec in (specs.copywriter, specs.designer, specs.reviewer)
    )
    per_pass = (
        timedelta(seconds=run_limits * time_multiplier)
        + 3 * _call_timeout(time_multiplier)
        + _image_call_bound()
    )
    return 3 * per_pass + timedelta(seconds=60)


def max_spec_duration_s() -> int:
    """Maior `max_duration_s` de TODOS os specs: Diretor, Copywriter, Designer e Revisor."""
    specs = _content_specs()
    return max(
        director_max_duration_s(),
        specs.copywriter.limits.max_duration_s,
        specs.designer.limits.max_duration_s,
        specs.reviewer.limits.max_duration_s,
    )


def worker_stale_after(time_multiplier: int = 1) -> timedelta:
    """Idade a partir da qual uma reserva aberta e de um processo morto, vista pelo worker.

    Os tempos de texto e dos specs usam o mesmo `time_multiplier` do motor e adaptadores.
    A execucao mais longa possivel de QUALQUER papel (o painel partilha a base com o worker):
    o maior `max_duration_s` mais uma chamada em excesso, a mais lenta de todas (a de imagem,
    `IMAGE_CALL_TIMEOUT_S`). Uma reserva mais nova que isto pode ser de uma execucao viva do
    outro processo; uma reserva de imagem em voo nunca e dada como orfa.
    """
    return timedelta(seconds=run_overrun_bound_s(max_spec_duration_s(), time_multiplier))


def log_time_settings(settings: Settings, process: str) -> None:
    """Regista (so nomes e numeros) o multiplicador e os tempos que dele derivam.

    O painel e o worker tem de usar o mesmo `LLM_TIME_MULTIPLIER`: comparar estas linhas do
    arranque de cada processo mostra uma deriva (o de multiplicador menor daria como orfa a
    reserva viva do outro).
    """
    multiplier = settings.llm_time_multiplier
    logger.info(
        "%s: LLM_TIME_MULTIPLIER=%d, lease do job=%d s, limiar das reservas=%d s",
        process,
        multiplier,
        content_job_lease(multiplier).total_seconds(),
        worker_stale_after(multiplier).total_seconds(),
    )


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
