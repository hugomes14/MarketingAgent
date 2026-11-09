"""Validacao em codigo do rascunho de texto: o que o esquema sozinho nao consegue garantir.

As violacoes voltam ao LLM como `extra_validator` do motor (contam como `invalid_output`) e
podem chegar ao `last_error` e a auditoria. Por isso cada mensagem e curta, cita a posicao ou
o campo e os numeros dos limites, e NUNCA o valor que o modelo escreveu.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from marketing_agent.content.schemas import RascunhoTexto
from marketing_agent.kb.service import KbEntry

PRICE_KIND = "preco"


@dataclass(frozen=True)
class ChannelLimits:
    """Limites de um canal; `max_hashtags` e `None` quando o canal nao fixa um maximo."""

    max_caption_chars: int
    max_hashtags: int | None


# Limites da spec 2A, secao 5 (valores fixos do contrato, nao configuracao).
CHANNEL_LIMITS: Mapping[str, ChannelLimits] = {
    "instagram": ChannelLimits(max_caption_chars=2200, max_hashtags=30),
    "facebook": ChannelLimits(max_caption_chars=5000, max_hashtags=None),
}

_CURRENCY = r"(?:€|EUR|USD|\$)"
# Valor com moeda depois (`12,50 €`, `10 EUR`, `5$`) ou antes (`€12`, `EUR 10`, `$5`). `EUR`
# sem `\b` a seguir apanha tambem `euros`. Sem moeda, um numero (`3 paes`) nao e preco.
_MONEY_RE = re.compile(
    rf"\d+(?:[.,]\d+)?\s?{_CURRENCY}|(?<![A-Za-z]){_CURRENCY}\s?\d", re.IGNORECASE
)


def channel_limits(channels: Sequence[str]) -> list[ChannelLimits]:
    """Limites dos canais pedidos; `ValueError` se faltam canais ou algum e desconhecido."""
    if not channels:
        raise ValueError("pelo menos um canal e obrigatorio")
    unknown = [channel for channel in channels if channel not in CHANNEL_LIMITS]
    if unknown:
        raise ValueError(f"canal desconhecido: {unknown!r}; validos: {sorted(CHANNEL_LIMITS)}")
    return [CHANNEL_LIMITS[channel] for channel in channels]


def validate_draft(
    draft: RascunhoTexto, *, channels: Sequence[str], valid_kb: Mapping[str, KbEntry]
) -> list[str]:
    """Devolve as violacoes do rascunho (lista vazia = valido); puro e determinista.

    `valid_kb` mapeia id -> item e deve conter so itens ativos e validos na data do post: aqui
    so se testa a pertenca. Com varios canais, vale o mais restritivo de cada limite. `channels`
    desconhecidos (ou nenhum) sao erro do chamador e levantam `ValueError`.
    """
    limits = channel_limits(channels)
    return [
        *_check_limits(draft, limits),
        *_check_alt_text(draft),
        *_check_hashtags(draft),
        *_check_claims(draft, valid_kb),
        *_check_price(draft, valid_kb),
    ]


def _check_limits(draft: RascunhoTexto, limits: Sequence[ChannelLimits]) -> list[str]:
    violations = []
    max_chars = min(item.max_caption_chars for item in limits)
    if len(draft.legenda) > max_chars:
        violations.append(
            f"legenda com {len(draft.legenda)} caracteres excede o maximo de {max_chars}"
        )
    max_tags = min((m for item in limits if (m := item.max_hashtags) is not None), default=None)
    if max_tags is not None and len(draft.hashtags) > max_tags:
        violations.append(f"{len(draft.hashtags)} hashtags excedem o maximo de {max_tags}")
    return violations


def _check_alt_text(draft: RascunhoTexto) -> list[str]:
    return [] if draft.alt_text.strip() else ["alt_text nao pode estar vazio"]


def _check_hashtags(draft: RascunhoTexto) -> list[str]:
    violations = []
    for index, tag in enumerate(draft.hashtags):
        if not tag.startswith("#") or len(tag) < 2:
            violations.append(f"hashtags[{index}] deve comecar por '#' e ter texto a seguir")
        elif any(char.isspace() for char in tag):
            violations.append(f"hashtags[{index}] nao pode ter espacos")
    return violations


def _check_claims(draft: RascunhoTexto, valid_kb: Mapping[str, KbEntry]) -> list[str]:
    return [
        f"alegacoes[{index}].kb_ref desconhecido ou sem validade"
        for index, claim in enumerate(draft.alegacoes)
        if claim.kb_ref is not None and claim.kb_ref not in valid_kb
    ]


def _check_price(draft: RascunhoTexto, valid_kb: Mapping[str, KbEntry]) -> list[str]:
    """Valor monetario na legenda ou no apelo exige alegacao ligada a um item `preco` valido.

    So se verifica a existencia do item, nao se o valor coincide com `price_amount`: essa
    comparacao e do Revisor (`preco_sem_validade`). O apelo entra para nao ser uma via de fuga.
    """
    fields = {"legenda": draft.legenda, "apelo_a_acao": draft.apelo_a_acao}
    priced = [name for name, text in fields.items() if _MONEY_RE.search(text)]
    if not priced or _cites_price_item(draft, valid_kb):
        return []
    return [
        f"{name} tem um valor monetario mas nenhuma alegacao cita um item de preco valido da base"
        for name in priced
    ]


def _cites_price_item(draft: RascunhoTexto, valid_kb: Mapping[str, KbEntry]) -> bool:
    return any(
        claim.kb_ref in valid_kb and valid_kb[claim.kb_ref].kind == PRICE_KIND
        for claim in draft.alegacoes
        if claim.kb_ref is not None
    )
