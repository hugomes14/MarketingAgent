"""Pecas de arranque partilhadas: duracoes dos specs e a lease dos trabalhos de conteudo."""

from datetime import timedelta

from marketing_agent.content.specs import load_content_specs
from marketing_agent.startup import content_job_lease, director_max_duration_s, worker_stale_after


def test_content_job_lease_covers_three_copywriter_and_reviewer_passes() -> None:
    specs = load_content_specs()
    passes = specs.copywriter.limits.max_duration_s + specs.reviewer.limits.max_duration_s
    assert content_job_lease() == timedelta(seconds=3 * passes + 60)


def test_content_job_lease_is_longer_than_the_director_rule_it_replaces() -> None:
    # Um job pode correr 3 x (180 + 180) s = 18 min; `director_max_duration_s() + 60` daria 5 min,
    # e outro worker reclamaria um job ainda vivo.
    assert content_job_lease() == timedelta(minutes=19)
    assert content_job_lease() > timedelta(seconds=director_max_duration_s() + 60) * 3


def test_worker_stale_reservation_threshold_is_the_longest_single_run() -> None:
    specs = load_content_specs()
    longest = max(
        director_max_duration_s(),
        specs.copywriter.limits.max_duration_s,
        specs.reviewer.limits.max_duration_s,
    )
    assert worker_stale_after() == timedelta(seconds=longest)
