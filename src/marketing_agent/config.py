"""Configuracao da aplicacao, lida de variaveis de ambiente e do ficheiro `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Capability = Literal["text", "image_input", "json_schema"]

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

    # hide_input_in_errors: com extra="forbid", uma chave desconhecida no .env (ex.: chave de API)
    # seria repetida na mensagem de erro; o erro continua a ser levantado, sem o valor.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True
    )

    # Obrigatorio e sem default: nunca apontar por omissao para a base de teste descartavel.
    database_url: str = Field(repr=False)
    monthly_budget_reference_usd: Decimal = Field(default=Decimal("20"), gt=0)
    budget_warn_percents: WarnPercents = (50, 80, 100)
    role_routes: dict[str, RoleRoute] = {}
    anthropic_api_key_file: Path | None = None
    openai_api_key_file: Path | None = None
    openai_base_url: str | None = None
    # Lista vazia nega todos os logins: falha segura quando nada foi configurado.
    panel_allowed_logins: list[str] = []
    panel_trusted_proxy: list[str] = ["127.0.0.1", "::1"]
    # Hostnames (sem porta) aceites no cabecalho Host: defesa contra DNS rebinding. Em producao
    # acrescentar o hostname do `tailscale serve`.
    panel_allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
    # Ficheiro com o segredo CSRF do painel (>= 32 caracteres); obrigatorio para servir o painel.
    panel_csrf_secret_file: Path | None = None
    max_message_chars: int = Field(default=4000, gt=0)
    # Tetos de publicacoes que o Diretor pode propor num plano semanal.
    plan_max_posts_per_week: int = Field(5, gt=0)
    plan_max_posts_per_day: int = Field(1, gt=0)
    model_prices_path: Path = Path("config/model_prices.yaml")

    @field_validator(
        "anthropic_api_key_file",
        "openai_api_key_file",
        "openai_base_url",
        "panel_csrf_secret_file",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """Uma variavel vazia (ex.: `OPENAI_BASE_URL=` no .env) equivale a nao definida."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


def get_settings() -> Settings:
    """Constroi as definicoes a partir do ambiente atual."""
    return Settings()
