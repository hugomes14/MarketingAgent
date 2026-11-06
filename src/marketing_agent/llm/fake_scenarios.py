"""Guioes prontos para o `FakeLlmClient`: entrevista, diagnostico, estrategia e falhas.

Os corpos JSON sao texto de `json.dumps`; os esquemas que os validam chegam mais tarde.
"""

import json
from typing import Final

from marketing_agent.budget.pricing import Usage
from marketing_agent.llm.types import LlmNetworkError, LlmResponse, StopReason

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


def _json_response(body: dict[str, object]) -> LlmResponse:
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
