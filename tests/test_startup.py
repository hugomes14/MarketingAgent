"""Pecas de arranque partilhadas: duracoes dos specs e a lease dos trabalhos de conteudo."""

import inspect
from datetime import timedelta

from marketing_agent.content.specs import load_content_specs
from marketing_agent.llm.anthropic_adapter import AnthropicClient
from marketing_agent.llm.openai_adapter import OpenAiClient
from marketing_agent.llm.types import LLM_CALL_TIMEOUT_S
from marketing_agent.startup import content_job_lease, director_max_duration_s, worker_stale_after


def test_adapters_use_the_shared_call_timeout() -> None:
    for adapter in (AnthropicClient, OpenAiClient):
        default = inspect.signature(adapter).parameters["timeout_s"].default
        assert default == LLM_CALL_TIMEOUT_S == 120.0


def test_content_job_lease_covers_three_passes_with_overrun_of_each_call() -> None:
    specs = load_content_specs()
    run_limits = specs.copywriter.limits.max_duration_s + specs.reviewer.limits.max_duration_s
    # O motor so verifica a duracao ANTES de cada passo: cada papel pode exceder o limite em
    # uma chamada (timeout do adaptador).
    per_pass = run_limits + 2 * LLM_CALL_TIMEOUT_S
    assert content_job_lease() == timedelta(seconds=3 * per_pass + 60)
    assert content_job_lease() == timedelta(minutes=31)  # 3 x (180 + 180 + 240) + 60 = 1860 s


def test_content_job_lease_is_longer_than_the_director_rule_it_replaces() -> None:
    assert content_job_lease() > timedelta(seconds=director_max_duration_s() + 60) * 3


def test_worker_stale_reservation_threshold_covers_the_longest_single_run() -> None:
    specs = load_content_specs()
    longest = max(
        director_max_duration_s(),
        specs.copywriter.limits.max_duration_s,
        specs.reviewer.limits.max_duration_s,
    )
    # Uma execucao pode ultrapassar `max_duration_s` em uma chamada: o limiar soma-a.
    assert worker_stale_after() == timedelta(seconds=longest + LLM_CALL_TIMEOUT_S)
    assert worker_stale_after() >= timedelta(seconds=LLM_CALL_TIMEOUT_S)
    assert worker_stale_after() < content_job_lease()  # uma execucao cabe numa lease
