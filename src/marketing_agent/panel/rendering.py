"""Renderizacao Jinja do painel: autoescape ligado, sem `safe`, caminhos relativos ao modulo."""

from decimal import Decimal
from pathlib import Path
from typing import Final

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from marketing_agent.panel.auth import TailscaleUser
from marketing_agent.panel.csrf import csrf_token_for

PANEL_DIR: Final = Path(__file__).resolve().parent
TEMPLATES_DIR: Final = PANEL_DIR / "templates"
STATIC_DIR: Final = PANEL_DIR / "static"

Context = dict[str, object]

_STATE_LABELS: Final = {
    "draft": "Rascunho",
    "submitted": "Aguarda decisão",
    "approved": "Aprovado",
    "rejected": "Rejeitado",
    "withdrawn": "Retirado",
    "open": "Em curso",
    "profile_requested": "Perfil pedido",
    "closed": "Fechada",
}


_GAP_STATUS_LABELS: Final = {
    "desconhecido": "Por saber",
    "suposicao": "Suposição",
    "confirmado": "Confirmado",
}
_ORIGIN_LABELS: Final = {
    "dito_pelo_responsavel": "Dito por si",
    "verificado": "Verificado",
    "suposto": "Suposto",
}


_KIND_LABELS: Final = {
    "oferta": "Ofertas",
    "preco": "Preços",
    "horario": "Horários",
    "contacto": "Contactos",
    "prova": "Provas",
    "politica": "Políticas",
    "outro": "Outros",
}
_CHANNEL_LABELS: Final = {"facebook": "Facebook", "instagram": "Instagram"}


_TOPIC_LABELS: Final = {
    "o_que_vende": "O que vende",
    "cliente_prioritario": "Cliente prioritário",
    "geografia": "Geografia",
    "origem_clientes_atual": "Origem atual dos clientes",
    "proposta_de_valor": "Proposta de valor",
    "motivos_compra_e_perda": "Motivos de compra e de perda",
    "capacidade_entrega": "Capacidade de entrega",
    "provas_disponiveis": "Provas disponíveis",
    "percurso_contacto": "Percurso de contacto",
    "voz_e_tom": "Voz e tom",
    "concorrentes": "Concorrentes",
    "objetivos": "Objetivos",
    "canais_e_idioma": "Canais e idioma",
    "material_existente": "Material existente",
}


def _usd(value: Decimal, places: int = 4) -> str:
    return f"{value:.{places}f}"


def _state_label(state: str) -> str:
    return _STATE_LABELS.get(state, state)


def _gap_status(status: str) -> str:
    return _GAP_STATUS_LABELS.get(status, status)


def _origin_label(origin: str) -> str:
    return _ORIGIN_LABELS.get(origin, origin)


def _kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def _channel_label(channel: str) -> str:
    return _CHANNEL_LABELS.get(channel, channel)


def _topic_label(topic: str) -> str:
    """Etiqueta em portugues de um topico do mapa; um desconhecido mostra-se sem sublinhados."""
    return _TOPIC_LABELS.get(topic) or topic.replace("_", " ").capitalize()


def build_environment() -> Environment:
    """Ambiente Jinja com autoescape para qualquer template e variaveis em falta como erro."""
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=StrictUndefined,
    )
    environment.filters["usd"] = _usd
    environment.filters["state_label"] = _state_label
    environment.filters["topic_label"] = _topic_label
    environment.filters["kind_label"] = _kind_label
    environment.filters["channel_label"] = _channel_label
    environment.filters["gap_status"] = _gap_status
    environment.filters["origin_label"] = _origin_label
    return environment


class Renderer:
    """Renderiza paginas completas (com utilizador e token CSRF) e fragmentos HTMX."""

    def __init__(self, csrf_secret: bytes) -> None:
        """Cria o ambiente Jinja e guarda o segredo com que se derivam os tokens CSRF."""
        self._environment = build_environment()
        self._csrf_secret = csrf_secret

    def page(
        self,
        template: str,
        user: TailscaleUser | None,
        context: Context | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        """Pagina completa; `user=None` (paginas de erro) omite o nome e o token CSRF."""
        token = csrf_token_for(user, self._csrf_secret) if user is not None else ""
        values: Context = {**(context or {}), "user": user, "csrf_token": token}
        return self.partial(template, values, status_code)

    def partial(
        self,
        template: str,
        context: Context,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> HTMLResponse:
        """Fragmento HTML (ou pagina ja com o seu contexto completo)."""
        body = self._environment.get_template(template).render(context)
        return HTMLResponse(body, status_code=status_code, headers=headers)

    def token_for(self, user: TailscaleUser) -> str:
        """Token CSRF do utilizador, para formularios dentro de fragmentos HTMX."""
        return csrf_token_for(user, self._csrf_secret)
