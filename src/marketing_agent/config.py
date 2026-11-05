"""Configuracao da aplicacao, lida de variaveis de ambiente e do ficheiro `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Capability = Literal["text", "image_input", "json_schema"]

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://marketing_agent:marketing_agent@127.0.0.1:5433/marketing_agent_test"
)

# Limiares de aviso do orcamento: nao vazio, cada um entre 1 e 100 por cento.
WarnPercents = Annotated[tuple[Annotated[int, Field(ge=1, le=100)], ...], Field(min_length=1)]


class RoleRoute(BaseModel):
    """Rota de um papel (ex.: diretor) para um modelo de um provedor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["fake", "anthropic", "openai"]
    model_id: str = Field(min_length=1)
    capabilities: frozenset[Capability]


class Settings(BaseSettings):
    """Definicoes da aplicacao.

    Campos compostos (`role_routes`, `budget_warn_percents`, `panel_allowed_logins`,
    `panel_trusted_proxy`) sao lidos do ambiente como JSON.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = DEFAULT_DATABASE_URL
    monthly_budget_reference_usd: Decimal = Field(default=Decimal("20"), gt=0)
    budget_warn_percents: WarnPercents = (50, 80, 100)
    role_routes: dict[str, RoleRoute] = {}
    anthropic_api_key_file: Path | None = None
    openai_api_key_file: Path | None = None
    openai_base_url: str | None = None
    # Lista vazia nega todos os logins: falha segura quando nada foi configurado.
    panel_allowed_logins: list[str] = []
    panel_trusted_proxy: list[str] = ["127.0.0.1", "::1"]
    max_message_chars: int = Field(default=4000, gt=0)
    model_prices_path: Path = Path("config/model_prices.yaml")


def get_settings() -> Settings:
    """Constroi as definicoes a partir do ambiente atual."""
    return Settings()
