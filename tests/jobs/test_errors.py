"""`describe_error`: nunca copia texto de modelo ou de utilizador para `last_error`."""

import pytest
from pydantic import BaseModel, ValidationError

from marketing_agent.budget.controller import RunCostLimitExceeded
from marketing_agent.harness.engine import NeedsHuman, RunLimitExceeded
from marketing_agent.jobs.errors import describe_error
from marketing_agent.jobs.queue import MAX_ERROR_CHARS
from marketing_agent.kb.service import KbError


class _Strict(BaseModel):
    count: int


class _SubclassedLimit(RunLimitExceeded):
    pass


def test_default_is_class_name_only() -> None:
    assert describe_error(KeyError("texto do utilizador")) == "KeyError"
    assert describe_error(RuntimeError("saida do modelo")) == "RuntimeError"
    assert describe_error(KeyError()) == "KeyError"


def test_needs_human_and_kb_error_are_class_name_only() -> None:
    assert describe_error(NeedsHuman("texto do modelo SEGREDO")) == "NeedsHuman"
    assert describe_error(KbError("key do utilizador")) == "KbError"


def test_validation_error_has_no_input() -> None:
    with pytest.raises(ValidationError) as caught:
        _Strict.model_validate({"count": "SEGREDO DO MODELO texto gerado pelo LLM"})
    assert "SEGREDO" in str(caught.value)  # o str() cru vazaria o texto
    assert describe_error(caught.value) == "ValidationError: 1 erros (int_parsing)"


def test_allow_listed_keeps_message() -> None:
    error = RunLimitExceeded("limite de 5 passos atingido")
    assert describe_error(error) == "RunLimitExceeded: limite de 5 passos atingido"
    assert describe_error(RunCostLimitExceeded("limite 1 USD")) == (
        "RunCostLimitExceeded: limite 1 USD"
    )


def test_allow_listed_is_single_line_and_truncated() -> None:
    summary = describe_error(RunLimitExceeded("linha longa\n" * 100))
    assert "\n" not in summary
    assert summary.startswith("RunLimitExceeded: linha longa linha longa")
    assert len(summary) == MAX_ERROR_CHARS


def test_subclass_of_allow_listed_type_is_class_name_only() -> None:
    # Tipo exato: uma subclasse futura nao herda o armazenamento da mensagem.
    assert describe_error(_SubclassedLimit("SEGREDO")) == "_SubclassedLimit"
