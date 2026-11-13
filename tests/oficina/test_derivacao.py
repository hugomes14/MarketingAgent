"""Estados fieis, JSON canonico limitado ao contrato publico e validacao de ETag."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from marketing_agent.oficina.estado import (
    ESTADO_TEXTO,
    Entrega,
    EstadoAgente,
    EstadoEquipa,
    RevisaoSemanal,
    RoleSignals,
    derive_state,
    etag_for,
    etag_matches,
    team_state_json,
    truncate_title,
)
from marketing_agent.oficina.papeis import PAPEIS


@pytest.mark.parametrize(
    "working,failure,stale,pending,expected",
    [
        (False, False, False, 0, "inativo"),
        (False, False, False, 2, "em_espera"),
        (False, False, True, 0, "desatualizado"),
        (False, False, True, 2, "desatualizado"),
        (False, True, False, 0, "erro"),
        (False, True, False, 2, "erro"),
        (False, True, True, 0, "erro"),
        (False, True, True, 2, "erro"),
        (True, False, False, 0, "a_trabalhar"),
        (True, False, False, 2, "a_trabalhar"),
        (True, False, True, 0, "a_trabalhar"),
        (True, False, True, 2, "a_trabalhar"),
        (True, True, False, 0, "a_trabalhar"),
        (True, True, False, 2, "a_trabalhar"),
        (True, True, True, 0, "a_trabalhar"),
        (True, True, True, 2, "a_trabalhar"),
    ],
)
def test_priority_order_each_combination(working, failure, stale, pending, expected):
    assert derive_state(RoleSignals(working, failure, stale, pending)) == expected


def test_every_state_has_portuguese_text():
    assert set(ESTADO_TEXTO.values()) == {
        "A trabalhar",
        "Erro por resolver",
        "Sem sinal: o processo pode ter parado",
        "À espera de si",
        "Sem trabalho",
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("curto", "curto"),
        (" \ntexto   com\t espacos\n", "texto com espacos"),
        ("x" * 60, "x" * 60),
        ("x" * 61, "x" * 59 + "…"),
        ("", ""),
    ],
)
def test_truncate_title(value, expected):
    assert truncate_title(value) == expected


def state():
    time = datetime(2026, 11, 13, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    item = Entrega("texto", "id", "Titulo", "/lote/2026-W46#post-id")
    agents = tuple(
        EstadoAgente(p, p.capitalize(), "inativo", ESTADO_TEXTO["inativo"], 0, time, (item,))
        for p in PAPEIS
    )
    return EstadoEquipa(agents, RevisaoSemanal("2026-W46", 0, "/lote/2026-W46"))


def test_json_is_canonical_and_stable():
    original = state()
    first = team_state_json(original)
    assert first == team_state_json(original)
    data = json.loads(first)
    assert (
        first
        == json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    assert set(data) == {"agentes", "revisao_semanal"}
    assert [a["papel"] for a in data["agentes"]] == list(PAPEIS)
    assert set(data["agentes"][0]) == {
        "papel",
        "nome",
        "estado",
        "estado_texto",
        "pendencias",
        "ultima_atividade_em",
        "entregas",
    }
    assert data["agentes"][0]["ultima_atividade_em"] == "2026-11-13T10:00:00Z"
    assert set(data["agentes"][0]["entregas"][0]) == {"tipo", "id", "titulo", "url"}
    assert set(data["revisao_semanal"]) == {"semana", "pendentes", "url"}


def test_null_activity_and_naive_timestamps():
    original = state()
    agent = original.agentes[0]
    no_activity = replace(original, agentes=(replace(agent, ultima_atividade_em=None),))
    assert json.loads(team_state_json(no_activity))["agentes"][0]["ultima_atividade_em"] is None
    naive = replace(original, agentes=(replace(agent, ultima_atividade_em=datetime(2026, 11, 13)),))
    with pytest.raises(ValueError):
        team_state_json(naive)


def test_etag_changes_with_content():
    original = state()
    body = team_state_json(original)
    etag = etag_for(body)
    assert etag.startswith('"') and etag.endswith('"') and len(etag) == 34
    assert etag == etag_for(body)
    changed = replace(original, revisao_semanal=RevisaoSemanal("2026-W46", 1, "/lote/2026-W46"))
    assert etag != etag_for(team_state_json(changed))


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, False),
        ("", False),
        ('"hash"', True),
        ('W/"hash"', True),
        ('"outro", W/"hash"', True),
        ("  *  ", True),
        ('"outro"', False),
        ("hash", False),
    ],
)
def test_etag_matches_parsing(header, expected):
    assert etag_matches(header, '"hash"') is expected
