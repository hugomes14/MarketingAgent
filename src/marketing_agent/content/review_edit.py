"""Edicao do responsavel: o texto novo, a sua validacao e a revisao que dela nasce.

A transacao, o bloqueio do post e o feedback sao do `ReviewService`; aqui so ha funcoes sem
efeitos na base, para a regra de validacao ser a mesma da geracao (`validate_draft`).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from marketing_agent.content.review_errors import InvalidEdit
from marketing_agent.content.revision_store import canonical_revision_hash
from marketing_agent.content.schemas import (
    MAX_ALT_TEXT_CHARS,
    MAX_CALL_TO_ACTION_CHARS,
    MAX_SCHEMA_CAPTION_CHARS,
    MAX_SCHEMA_HASHTAGS,
    Alegacao,
    Hashtag,
    RascunhoTexto,
)
from marketing_agent.content.validation import validate_draft
from marketing_agent.db.models import Post, PostRevision
from marketing_agent.kb.service import KbEntry

EDIT_ORIGIN = "edicao_responsavel"
# A mensagem junta as violacoes (uma por hashtag/alegacao no pior caso): limite-se o tamanho.
MAX_SUMMARY_CHARS = 500


class CaptionEdit(BaseModel):
    """Campos que o responsavel pode alterar; as alegacoes mantem-se as da revisao base."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    legenda: str = Field(min_length=1, max_length=MAX_SCHEMA_CAPTION_CHARS)
    hashtags: list[Hashtag] = Field(max_length=MAX_SCHEMA_HASHTAGS)
    apelo_a_acao: str = Field(max_length=MAX_CALL_TO_ACTION_CHARS)
    alt_text: str = Field(max_length=MAX_ALT_TEXT_CHARS)


def check_edit(
    base: PostRevision,
    changes: CaptionEdit,
    channels: Sequence[str],
    valid_kb: Mapping[str, KbEntry],
) -> None:
    """Levanta `InvalidEdit` se o texto novo viola as regras ou nao altera nada.

    As alegacoes sao as da revisao base: se o item da base que as apoiava deixou de ser valido
    na data do post, a edicao falha (`alegacoes[i].kb_ref ...`) e o caminho e pedir alteracoes.
    O texto do responsavel nunca entra nas mensagens, so campos e contagens.
    """
    draft = RascunhoTexto(
        legenda=changes.legenda,
        hashtags=changes.hashtags,
        apelo_a_acao=changes.apelo_a_acao,
        alt_text=changes.alt_text,
        alegacoes=[Alegacao.model_validate(claim) for claim in base.claims],
    )
    violations = validate_draft(draft, channels=channels, valid_kb=valid_kb)
    if not draft.legenda.strip():
        violations.insert(0, "legenda nao pode estar vazia")
    if violations:
        summary = "edicao invalida: " + "; ".join(violations)
        raise InvalidEdit(summary[:MAX_SUMMARY_CHARS], tuple(violations))
    if edit_hash(base, changes, channels) == base.revision_hash:
        raise InvalidEdit("edicao sem alteracoes", ())


def edit_hash(base: PostRevision, changes: CaptionEdit, channels: Sequence[str]) -> str:
    """Hash canonico do texto editado (as alegacoes vem da revisao base)."""
    return canonical_revision_hash(
        caption=changes.legenda,
        hashtags=changes.hashtags,
        call_to_action=changes.apelo_a_acao,
        alt_text=changes.alt_text,
        claims=base.claims,
        channels=channels,
    )


def build_edit_revision(
    post: Post, base: PostRevision, changes: CaptionEdit, now: datetime
) -> PostRevision:
    """Revisao `edicao_responsavel` a seguir a `base` (o chamador tem o post bloqueado).

    Nao ha parecer do Revisor nem execucao do agente: a edicao e do responsavel.
    """
    return PostRevision(
        brand_id=post.brand_id,
        post_id=post.id,
        rev_no=base.rev_no + 1,
        revision_hash=edit_hash(base, changes, post.channels),
        caption=changes.legenda,
        hashtags=list(changes.hashtags),
        call_to_action=changes.apelo_a_acao,
        alt_text=changes.alt_text,
        claims=[dict(claim) for claim in base.claims],
        review_verdict=None,
        review_violations=[],
        review_notes=None,
        origin=EDIT_ORIGIN,
        produced_by_run_id=None,
        created_at=now,
    )
