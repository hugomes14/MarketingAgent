"""Configuracao da aplicacao, lida de variaveis de ambiente e do ficheiro `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Capability = Literal["text", "image_input", "image_output", "json_schema"]

# Teto de custo (USD) de UM pacote texto e imagem, somando todas as tentativas e voltas do pedido.
DEFAULT_PACKAGE_MAX_COST_USD = Decimal("1.50")

# Papel que gera imagens (fundos). Nao e um papel de texto: o router LLM ignora esta rota e a
# fabrica de imagens (`images.factory`) e a unica que a le.
IMAGE_ROUTE = "designer_imagem"

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
    # Amplia so os tempos (chamada e execucao), nunca tokens/custos/passos.
    llm_time_multiplier: int = Field(default=1, ge=1, le=10)
    # Chave propria das imagens (fatura a parte); nunca reutiliza a do texto nem `openai_base_url`.
    openai_images_api_key_file: Path | None = None
    image_quality: Literal["low", "medium", "high"] = "medium"
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
    # Teto de custo de um pacote (texto e imagem) em todas as tentativas e voltas do pedido; vale
    # igual para o percurso habitual e para a comparacao (nenhum dos dois gasta mais que o outro).
    package_max_cost_usd: Decimal = Field(DEFAULT_PACKAGE_MAX_COST_USD, gt=0)
    model_prices_path: Path = Path("config/model_prices.yaml")
    media_dir: Path = Path("var/media")
    # Diretorio dos conjuntos de avaliacao versionados da comparacao (`<nome>.yaml`).
    evals_dir: Path = Path("evals")

    @field_validator(
        "anthropic_api_key_file",
        "openai_api_key_file",
        "openai_images_api_key_file",
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

    @model_validator(mode="after")
    def _check_image_route(self) -> "Settings":
        """So `designer_imagem` gera imagens, e so pela OpenAI (unico gerador implementado).

        Nenhuma outra rota pode declarar `image_output`: o router de texto nunca deve escolher
        um modelo de imagem para um papel de texto.
        """
        for role, route in self.role_routes.items():
            declares_image = "image_output" in route.capabilities
            if role != IMAGE_ROUTE:
                if declares_image:
                    raise ValueError(f"so a rota {IMAGE_ROUTE!r} pode declarar 'image_output'")
            elif route.provider != "openai" or not declares_image:
                raise ValueError(
                    f"a rota {IMAGE_ROUTE!r} exige provider 'openai' e a capacidade 'image_output'"
                )
        return self


def get_settings() -> Settings:
    """Constroi as definicoes a partir do ambiente atual."""
    return Settings()
