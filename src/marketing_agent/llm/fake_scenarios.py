"""Guioes prontos para o `FakeLlmClient`: entrevista, diagnostico, estrategia, plano, texto, falhas.

Os corpos JSON sao texto de `json.dumps`; os esquemas que os validam chegam mais tarde.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Final

from marketing_agent.budget.pricing import Usage
from marketing_agent.llm.types import LlmNetworkError, LlmResponse, StopReason
from marketing_agent.planning.validation import week_range

Script = list[LlmResponse | Exception]

# Lista provisoria dos 14 topicos do mapa de lacunas; a definitiva chega na Tarefa 11.
_GAP_TOPICS: Final = (
    "o_que_vende",
    "cliente_prioritario",
    "geografia",
    "origem_clientes_atual",
    "proposta_de_valor",
    "motivos_compra_e_perda",
    "capacidade_entrega",
    "provas_disponiveis",
    "percurso_contacto",
    "voz_e_tom",
    "concorrentes",
    "objetivos",
    "canais_e_idioma",
    "material_existente",
)

_QUESTIONS: Final = (
    "Que produtos ou servicos vendem e quais dao mais margem?",
    "Quem e o cliente ideal e onde esta?",
    "Como chegam hoje os vossos clientes?",
    "Que provas tem, como avaliacoes ou casos reais?",
    "Qual e a capacidade de entrega por mes?",
)


def _response(text: str, stop_reason: StopReason = StopReason.end) -> LlmResponse:
    return LlmResponse(
        text=text,
        usage=Usage(input_tokens=100, output_tokens=50),
        provider_request_id=None,
        resolved_model="fake-1",
        stop_reason=stop_reason,
    )


def _json_response(body: Mapping[str, object]) -> LlmResponse:
    return _response(json.dumps(body, ensure_ascii=False))


def _interview_turn(summary: str, question: str | None, topics: list[str]) -> dict[str, object]:
    statuses = ("confirmado", "suposicao", "desconhecido")
    origins = ("dito_pelo_responsavel", "suposto", None)
    updates = [
        {
            "topic": topic,
            "status": statuses[i % 3],
            "note": f"Nota sobre {topic.replace('_', ' ')}.",
            "origin": origins[i % 3],
        }
        for i, topic in enumerate(topics)
    ]
    return {
        "next_question": question,
        "summary": summary,
        "gap_map_updates": updates,
        "ready_for_profile": question is None,
        "injection_suspected": False,
    }


def _profile() -> dict[str, object]:
    topics = {
        "o_que_vende": {"texto": "Pao artesanal e pastelaria.", "origem": "dito_pelo_responsavel"},
        "cliente_prioritario": {"texto": "Familias do bairro.", "origem": "dito_pelo_responsavel"},
        "geografia": {"texto": "Porto e arredores.", "origem": "verificado"},
        "objetivos": {"texto": "Mais encomendas de fim de semana.", "origem": "suposto"},
    }
    return {
        "topicos": topics,
        "lacunas_abertas": ["Margem por produto desconhecida."],
        "suposicoes": ["Os clientes preferem levantar na loja."],
    }


def interview_full(turns: int = 3) -> Script:
    """Primeira pergunta, `turns` respostas (a ultima pede o perfil) e o perfil final."""
    script: Script = [_json_response(_interview_turn("", _QUESTIONS[0], []))]
    for turn in range(turns):
        is_last = turn == turns - 1
        question = None if is_last else _QUESTIONS[(turn + 1) % len(_QUESTIONS)]
        topics = [_GAP_TOPICS[(turn * 3 + k) % len(_GAP_TOPICS)] for k in range(3)]
        script.append(
            _json_response(_interview_turn(f"Resumo do turno {turn + 1}.", question, topics))
        )
    script.append(_json_response(_profile()))
    return script


def _diagnosis() -> dict[str, object]:
    return {
        "oferta_prioritaria": "Cabazes semanais de pao e pastelaria.",
        "cliente_prioritario": "Familias do bairro com compra recorrente.",
        "geografia": "Porto e arredores.",
        "motivos_compra": "Qualidade e proximidade.",
        "motivos_perda": "Preco acima do supermercado.",
        "capacidade_entrega": "Cerca de 80 encomendas por semana.",
        "provas_disponiveis": "Avaliacoes de clientes e fotos do forno.",
        "percurso_contacto": "Instagram, mensagem directa, levantamento na loja.",
        "adequacao_canais": "Instagram adequado; Google Maps ainda por trabalhar.",
        "achados": [
            {
                "afirmacao": "A maior parte dos clientes chega por recomendacao.",
                "tipo": "facto",
                "evidencia": "Dito pelo responsavel na entrevista.",
                "origem": "dito_pelo_responsavel",
            },
            {
                "afirmacao": "Fotos de producao poderiam aumentar as encomendas.",
                "tipo": "hipotese",
                "evidencia": None,
                "origem": None,
            },
        ],
        "riscos": ["Dependencia de um unico canal."],
        "alternativas": ["Focar em empresas locais para pequenos-almocos."],
    }


def _strategy() -> dict[str, object]:
    return {
        "objetivos": [
            {"descricao": "Mais encomendas", "indicador": "encomendas por semana", "alvo": "100"},
            {"descricao": "Mais visibilidade", "indicador": "pesquisas locais", "alvo": None},
        ],
        "posicionamento": "A padaria do bairro com receitas de familia.",
        "posicionamento_estado": "hipotese",
        "pilares": ["Producao artesanal", "Proximidade", "Prova social"],
        "propostas_oferta": [
            {"proposta": "Cabaz semanal", "justificacao": "Gera receita recorrente."},
            {
                "proposta": "Pack para empresas",
                "justificacao": "Aproveita capacidade de dias uteis.",
            },
        ],
        "experiencias": [
            {
                "hipotese": "Fotos de producao aumentam mensagens",
                "metrica": "mensagens",
                "prazo_dias": 30,
            }
        ],
        "indicadores_minimos": ["encomendas por semana", "mensagens recebidas"],
    }


def diagnosis_and_strategy() -> Script:
    """Diagnostico seguido da estrategia."""
    return [_json_response(_diagnosis()), _json_response(_strategy())]


_PLAN_THEMES: Final = (
    ("Pao do dia saido do forno", "Producao artesanal"),
    ("Os vizinhos que nos visitam", "Proximidade"),
    ("O que dizem os clientes", "Prova social"),
)
_PLAN_CHANNELS: Final = (["instagram"], ["facebook", "instagram"], ["facebook"])


def weekly_plan(week: str, slots: int = 3) -> Script:
    """Plano valido de `slots` publicacoes (1 a 14) na semana ISO `week`, sem `kb_refs`.

    Espalha os dias com salto de 2 (seg, qua, sex, dom, ...): ate 7 slots ficam um por dia.
    A primeira publicacao liga-se a experiencia 0 da estrategia do cenario `_strategy`.
    """
    monday, _ = week_range(week)
    plan_slots = []
    for index in range(slots):
        theme, pillar = _PLAN_THEMES[index % len(_PLAN_THEMES)]
        plan_slots.append(
            {
                "slot_key": f"{week}-{index + 1}",
                "data": (monday + timedelta(days=index * 2 % 7)).isoformat(),
                "canais": _PLAN_CHANNELS[index % len(_PLAN_CHANNELS)],
                "tema": theme,
                "pilar": pillar,
                "objetivo": "Gerar mensagens e encomendas.",
                "experiment_index": 0 if index == 0 else None,
                "kb_refs": [],
                "justificacao": f"Reforca o pilar {pillar.lower()} com um tema concreto.",
            }
        )
    body: dict[str, object] = {
        "semana": week,
        "slots": plan_slots,
        "suposicoes": ["A capacidade de entrega suporta o numero de publicacoes proposto."],
    }
    return [_json_response(body)]


_DEFAULT_HASHTAGS: Final = ("#padariadobairo", "#paoartesanal")


def _caption_body(
    legenda: str,
    hashtags: Sequence[str],
    apelo_a_acao: str,
    alt_text: str,
    alegacoes: Sequence[Mapping[str, str | None]],
) -> dict[str, object]:
    return {
        "legenda": legenda,
        "hashtags": list(hashtags),
        "apelo_a_acao": apelo_a_acao,
        "alt_text": alt_text,
        "alegacoes": [dict(claim) for claim in alegacoes],
    }


def caption_draft(
    legenda: str = "Pao quente todas as manhas, feito com receitas de familia.",
    *,
    hashtags: Sequence[str] = _DEFAULT_HASHTAGS,
    apelo_a_acao: str = "Passe pela loja e experimente.",
    alt_text: str = "Pao artesanal acabado de sair do forno.",
    alegacoes: Sequence[Mapping[str, str | None]] = (),
) -> Script:
    """Rascunho de texto valido por omissao (sem precos nem `kb_ref`); cada campo e ajustavel.

    `alegacoes` e uma lista de `{"texto": ..., "kb_ref": id ou None}`.
    """
    return [_json_response(_caption_body(legenda, hashtags, apelo_a_acao, alt_text, alegacoes))]


_VISUAL_DEFAULTS: Final[dict[str, str | None]] = {
    "layout": "fundo_ia_titulo",
    "foto_id": None,
    "titulo": "Pao quente",
    "tema_fundo": "manha de verao com luz dourada",
    "estilo": "aguarela",
    "alt_text": "Pao acabado de sair do forno sobre um fundo de aguarela.",
    "justificacao": "Sem foto adequada, um fundo abstrato com o titulo.",
}


def _visual_body(campos: Mapping[str, str | None]) -> dict[str, str | None]:
    unknown = sorted(set(campos) - set(_VISUAL_DEFAULTS))
    if unknown:
        raise ValueError(f"campo desconhecido de RascunhoVisual: {unknown}")
    return {**_VISUAL_DEFAULTS, **campos}


def visual_draft(**campos: str | None) -> Script:
    """Rascunho visual valido por omissao (`fundo_ia_titulo`); cada campo e ajustavel.

    `campos` substitui os campos de `RascunhoVisual` pelo nome (`None` anula um campo), por
    exemplo `visual_draft(layout="foto_titulo", foto_id="f1", tema_fundo=None, estilo=None)`.
    Nome de campo desconhecido levanta `ValueError`.
    """
    return [_json_response(_visual_body(campos))]


def simple_package(
    legenda: str = "Pao quente todas as manhas, feito com receitas de familia.",
    *,
    hashtags: Sequence[str] = _DEFAULT_HASHTAGS,
    apelo_a_acao: str = "Passe pela loja e experimente.",
    alt_text: str = "Pao artesanal acabado de sair do forno.",
    alegacoes: Sequence[Mapping[str, str | None]] = (),
    visual: Mapping[str, str | None] | None = None,
) -> Script:
    """Pacote do percurso simples: UMA resposta com `texto` e `visual` validos por omissao.

    Os campos do texto sao os de `caption_draft`; `alt_text` e o do texto. `visual` substitui
    campos de `RascunhoVisual` pelo nome, como em `visual_draft` (por omissao `fundo_ia_titulo`):
    `simple_package(visual={"layout": "foto_titulo", "foto_id": "f1", "tema_fundo": None,
    "estilo": None})`. Nome de campo desconhecido levanta `ValueError`.
    """
    body: dict[str, object] = {
        "texto": _caption_body(legenda, hashtags, apelo_a_acao, alt_text, alegacoes),
        "visual": _visual_body(visual or {}),
    }
    return [_json_response(body)]


def review(
    veredicto: str,
    violacoes: Sequence[str] = (),
    notas: str = "",
    *,
    imagem_verificada: bool = False,
) -> Script:
    """Parecer do Revisor com o veredicto e as violacoes dados (strings do enum `Violacao`).

    `imagem_verificada` e falso por omissao (parecer de um pacote so de texto); os cenarios de
    pacotes com imagem passam `imagem_verificada=True`.
    """
    body: dict[str, object] = {
        "veredicto": veredicto,
        "violacoes": list(violacoes),
        "notas": notas or f"Parecer automatico: {veredicto}.",
        "imagem_verificada": imagem_verificada,
    }
    return [_json_response(body)]


def invalid_then_valid(valid_json: str) -> Script:
    """Uma resposta que nao e JSON e depois a resposta valida."""
    return [_response("isto nao e json {"), _response(valid_json)]


def always_invalid() -> Script:
    """Tres respostas que nao sao JSON."""
    return [_response("nao e json"), _response("continua a nao ser json"), _response("[1, 2")]


def refusal() -> Script:
    """Uma recusa do modelo, com texto curto."""
    return [_response("Nao posso ajudar com isso.", StopReason.refusal)]


def truncated() -> Script:
    """Uma resposta cortada por limite de tokens, com JSON incompleto."""
    return [_response('{"next_question": "Que produtos ven', StopReason.max_tokens)]


def network_error_then(valid_json: str) -> Script:
    """Uma falha de rede e depois a resposta valida."""
    return [LlmNetworkError("falha de rede simulada"), _response(valid_json)]
