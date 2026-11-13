"""Leitura readonly do trabalho real, incluindo recuperacao e isolamento por marca."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event
from team_seed import team_queries, team_seed

from marketing_agent.db.models import Brand, Post
from marketing_agent.oficina.estado import team_state_json

__all__ = ["team_queries", "team_seed"]


def agents(query, seed):
    return {a.papel: a for a in query.team_state(seed.brand_id).agentes}


def test_idle_team_all_inativo(team_seed, team_queries):
    assert {a.estado for a in agents(team_queries, team_seed).values()} == {"inativo"}


def test_running_run_within_max_duration_is_a_trabalhar(team_seed, team_queries):
    team_seed.seed_run("designer", "running")
    data = agents(team_queries, team_seed)
    assert data["designer"].estado == "a_trabalhar"
    assert data["copywriter"].estado == "inativo"


def test_running_job_attributed_to_current_role(team_seed, team_queries):
    seed = team_seed
    post = seed.seed_post("drafting")
    now = seed.clock.now()
    seed.seed_run(
        "copywriter", "succeeded", now - timedelta(seconds=20), now - timedelta(seconds=10)
    )
    seed.seed_run("revisor", "running", now - timedelta(seconds=5))
    seed.seed_job(post_id=post.id, updated_at=now - timedelta(seconds=30))
    data = agents(team_queries, seed)
    assert data["revisor"].estado == "a_trabalhar" and data["copywriter"].estado == "inativo"


def test_newly_claimed_job_without_run_shows_copywriter(team_seed, team_queries):
    post = team_seed.seed_post("drafting")
    team_seed.seed_job(post_id=post.id)
    assert agents(team_queries, team_seed)["copywriter"].estado == "a_trabalhar"


def test_worker_killed_mid_job_shows_desatualizado(team_seed, team_queries):
    seed = team_seed
    post = seed.seed_post("drafting")
    now = seed.clock.now()
    seed.seed_run("revisor", "running", now - timedelta(minutes=9))
    seed.seed_job(
        post_id=post.id,
        updated_at=now - timedelta(minutes=10),
        locked_until=now - timedelta(seconds=1),
    )
    assert agents(team_queries, seed)["revisor"].estado == "desatualizado"


def test_orphan_run_superseded_by_newer_run(team_seed, team_queries):
    seed = team_seed
    seed.seed_run("copywriter", "running", seed.clock.now() - timedelta(hours=1))
    assert agents(team_queries, seed)["copywriter"].estado == "desatualizado"
    seed.seed_run("copywriter", "succeeded")
    assert agents(team_queries, seed)["copywriter"].estado == "inativo"


@pytest.mark.parametrize("status", ["failed", "needs_human"])
def test_last_run_failed_or_needs_human_is_erro_until_next_success(team_seed, team_queries, status):
    seed = team_seed
    seed.seed_run("diretor", status)
    assert agents(team_queries, seed)["diretor"].estado == "erro"
    seed.clock.advance(timedelta(seconds=1))
    seed.seed_run("diretor", "succeeded")
    assert agents(team_queries, seed)["diretor"].estado == "inativo"


@pytest.mark.parametrize("resolution", ["regenerated", "cancelled", "approved", "rejected"])
def test_dead_job_unresolved_until_post_regenerated_or_cancelled(
    team_seed, team_queries, resolution
):
    seed = team_seed
    post = seed.seed_post("drafting", last_error="ERROR-PRIVATE")
    seed.seed_job("dead", post.id)
    assert agents(team_queries, seed)["copywriter"].estado == "erro"
    assert (
        team_queries.agent_panel(seed.brand_id, "copywriter").falhas[0].last_error
        == "ERROR-PRIVATE"
    )
    if resolution == "regenerated":
        seed.clock.advance(timedelta(seconds=1))
        seed.seed_job("queued", post.id)
    else:
        with seed.factory.begin() as session:
            session.get_one(Post, post.id).lifecycle = resolution
    assert agents(team_queries, seed)["copywriter"].estado == "inativo"


def test_eval_job_failures_do_not_raise_erro(team_seed, team_queries):
    team_seed.seed_job("dead", kind="eval_package")
    assert {a.estado for a in agents(team_queries, team_seed).values()} == {"inativo"}


def test_manual_review_job_belongs_to_revisor(team_seed, team_queries):
    post = team_seed.seed_post("in_review")
    team_seed.seed_job(post_id=post.id, kind="review_post")
    assert agents(team_queries, team_seed)["revisor"].estado == "a_trabalhar"


def test_pending_counts_per_role(team_seed, team_queries):
    seed = team_seed
    for kind in ("perfil_marca", "diagnostico", "estrategia", "plano"):
        seed.seed_artifact(kind)
    asset = seed.seed_asset()
    for rounds, verdict, image in (
        (0, "aprovar", None),
        (1, "rever", None),
        (2, "rever", asset.id),
        (2, "aprovar", asset.id),
        (2, None, None),
    ):
        post = seed.seed_post(review_rounds=rounds)
        seed.seed_revision(post.id, verdict, image)
    result = agents(team_queries, seed)
    assert [result[p].pendencias for p in ("diretor", "copywriter", "designer", "revisor")] == [
        4,
        5,
        2,
        1,
    ]


def test_entregas_max_five_truncated_with_internal_urls(team_seed, team_queries):
    seed = team_seed
    created = []
    for index in range(8):
        post = seed.seed_post(tema=f"{index} " + "tema " * 50)
        seed.seed_revision(post.id)
        created.append(post.id)
    deliveries = agents(team_queries, seed)["copywriter"].entregas
    assert [e.id for e in deliveries] == list(reversed(created[-5:]))
    assert all(
        len(e.titulo) <= 60 and e.titulo.endswith("…") and e.url.startswith("/lote/2026-W46#post-")
        for e in deliveries
    )
    assert "CAPTION-PRIVATE" not in team_state_json(team_queries.team_state(seed.brand_id)).decode()


def test_same_post_same_url_across_roles(team_seed, team_queries):
    seed = team_seed
    post = seed.seed_post(review_rounds=2)
    seed.seed_revision(post.id, "rever", seed.seed_asset().id)
    result = agents(team_queries, seed)
    urls = {result[p].entregas[0].url for p in ("copywriter", "designer", "revisor")}
    assert urls == {f"/lote/2026-W46#post-{post.id}"}


def test_weekly_review_week_and_count(team_seed, team_queries):
    seed = team_seed
    empty = team_queries.team_state(seed.brand_id).revisao_semanal
    assert (empty.semana, empty.pendentes) == ("2026-W46", 0)
    seed.seed_post(slot_key="2026-W47-1")
    seed.seed_post(slot_key="2026-W45-1")
    seed.seed_post(slot_key="2026-W45-2")
    week = team_queries.team_state(seed.brand_id).revisao_semanal
    assert (week.semana, week.pendentes, week.url) == ("2026-W45", 2, "/lote/2026-W45")


def test_other_brand_data_ignored(team_seed, team_queries):
    seed = team_seed
    with seed.factory.begin() as session:
        brand = Brand(name="Outra")
        session.add(brand)
        session.flush()
    other = replace(seed, brand_id=brand.id)
    other.seed_run("diretor", "running")
    other.seed_post()
    assert {a.estado for a in agents(team_queries, seed).values()} == {"inativo"}


def test_state_query_count_is_bounded(team_seed, team_queries):
    seed = team_seed
    bind = seed.factory.kw["bind"]
    statements = []

    def count(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    def measured():
        statements.clear()
        event.listen(bind, "before_cursor_execute", count)
        try:
            team_queries.team_state(seed.brand_id)
        finally:
            event.remove(bind, "before_cursor_execute", count)
        return len(statements)

    empty = measured()
    posts = [seed.seed_post() for _ in range(200)]
    for index in range(500):
        seed.seed_run(("copywriter", "designer", "revisor")[index % 3], "succeeded")
    for index in range(300):
        seed.seed_job("dead", posts[index % 200].id)
    assert measured() == empty and empty <= 10


def test_priority_between_signals(team_seed, team_queries):
    from marketing_agent.db.models import Job

    seed = team_seed
    pending = seed.seed_post()
    live = seed.seed_job(post_id=seed.seed_post("drafting").id)
    stale = seed.seed_job(
        post_id=seed.seed_post("drafting").id, locked_until=seed.clock.now() - timedelta(seconds=1)
    )
    seed.seed_run("copywriter", "failed")
    assert agents(team_queries, seed)["copywriter"].estado == "a_trabalhar"
    with seed.factory.begin() as session:
        session.get_one(Job, live.id).status = "cancelled"
    assert agents(team_queries, seed)["copywriter"].estado == "erro"
    seed.clock.advance(timedelta(seconds=1))
    seed.seed_run("copywriter", "succeeded")
    assert agents(team_queries, seed)["copywriter"].estado == "desatualizado"
    with seed.factory.begin() as session:
        session.get_one(Job, stale.id).status = "cancelled"
    assert agents(team_queries, seed)["copywriter"].estado == "em_espera"
    with seed.factory.begin() as session:
        session.get_one(Post, pending.id).lifecycle = "approved"
    assert agents(team_queries, seed)["copywriter"].estado == "inativo"


def test_claim_window_uses_lease_when_updated_at_is_old(team_seed, team_queries):
    from marketing_agent.startup import content_job_lease

    seed = team_seed
    post = seed.seed_post("drafting")
    now = seed.clock.now()
    seed.seed_run("copywriter", "succeeded", now - timedelta(hours=1), now - timedelta(minutes=59))
    seed.seed_job(
        post_id=post.id,
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
        locked_until=now + content_job_lease(time_multiplier=1),
    )
    seed.clock.advance(timedelta(seconds=1))
    seed.seed_run("revisor", "running")
    data = agents(team_queries, seed)
    assert data["revisor"].estado == "a_trabalhar" and data["copywriter"].estado == "inativo"


def test_director_deliveries_ordered_by_transition_timestamp(team_seed, team_queries):
    ids = [team_seed.seed_artifact("perfil_marca").id for _ in range(8)]
    director = agents(team_queries, team_seed)["diretor"]
    assert director.pendencias == 8
    assert [d.id for d in director.entregas] == list(reversed(ids[-5:]))
    assert all(d.titulo == "Perfil de marca" for d in director.entregas)


@pytest.fixture
def office_panel(make_panel):
    return make_panel([])


def test_estado_requires_auth_and_rejects_unknown_host(office_panel):
    from fastapi.testclient import TestClient

    panel = office_panel
    assert panel.client.get("/oficina/estado").status_code == 401
    assert (
        panel.get("/oficina/estado", {"Tailscale-User-Login": "outsider@example.com"}).status_code
        == 403
    )
    untrusted = TestClient(panel.app, client=("198.51.100.8", 5000))
    assert untrusted.get("/oficina/estado", headers=panel.headers()).status_code == 403
    assert panel.get("/oficina/estado", {"Host": "evil.example"}).status_code == 400


def test_estado_json_shape_and_etag_304(office_panel):
    from team_seed import TeamSeed

    panel = office_panel
    first = panel.get("/oficina/estado")
    assert first.status_code == 200 and first.headers["content-type"].startswith("application/json")
    data = first.json()
    assert set(data) == {"agentes", "revisao_semanal"}
    assert [a["papel"] for a in data["agentes"]] == ["diretor", "copywriter", "designer", "revisor"]
    etag = first.headers["etag"]
    cached = panel.get("/oficina/estado", {"If-None-Match": f'"outro", W/{etag}'})
    assert cached.status_code == 304 and cached.content == b"" and cached.headers["etag"] == etag
    seed = TeamSeed(panel.factory, panel.clock, panel.brand_id)
    seed.seed_run("copywriter", "running")
    changed = panel.get("/oficina/estado", {"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert (
        next(a for a in changed.json()["agentes"] if a["papel"] == "copywriter")["estado"]
        == "a_trabalhar"
    )


def test_estado_no_store_and_security_headers_on_200_and_304(office_panel):
    panel = office_panel
    first = panel.get("/oficina/estado")
    assert first.status_code == 200
    second = panel.get("/oficina/estado", {"If-None-Match": first.headers["etag"]})
    for response in (first, second):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "same-origin"
        assert (
            response.headers["content-security-policy"]
            == "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )


def test_estado_has_no_full_text_or_personal_data(office_panel):
    from team_seed import TeamSeed

    panel = office_panel
    seed = TeamSeed(panel.factory, panel.clock, panel.brand_id)
    post = seed.seed_post("drafting", tema="TEMA " * 50, last_error="ERROR-PRIVATE")
    seed.seed_revision(post.id, caption="CAPTION-PRIVATE")
    seed.seed_job("dead", post.id)
    response = panel.get("/oficina/estado")
    assert response.status_code == 200
    assert all(
        marker not in response.text
        for marker in ("ERROR-PRIVATE", "CAPTION-PRIVATE", "NOTES-PRIVATE", "OWNER-PRIVATE")
    )
    titles = [d["titulo"] for a in response.json()["agentes"] for d in a["entregas"]]
    assert all(len(t) <= 60 for t in titles)


def test_estado_reads_do_not_write(office_panel):
    import json

    from sqlalchemy import text

    from marketing_agent.db.base import Base

    panel = office_panel

    def snapshot():
        with panel.factory() as session:
            return {
                name: sorted(
                    json.dumps(row, sort_keys=True)
                    for row in session.scalars(text(f'SELECT to_jsonb(t) FROM "{name}" t'))
                )
                for name in Base.metadata.tables
            }

    before = snapshot()
    for _ in range(10):
        assert panel.get("/oficina/estado").status_code == 200
    assert snapshot() == before and panel.llm.requests == []


def test_oficina_routes_are_get_only(office_panel):
    from fastapi.routing import iter_route_contexts

    routes = [
        r
        for r in iter_route_contexts(office_panel.app.routes)
        if r.path and r.path.startswith("/oficina")
    ]
    assert routes and all(r.methods and r.methods <= {"GET", "HEAD"} for r in routes)
    assert office_panel.post("/oficina/estado").status_code == 405


def test_estado_uses_effective_role_duration(make_panel):
    from team_seed import TeamSeed

    from marketing_agent.startup import role_max_durations

    panel = make_panel([], settings_overrides={"llm_time_multiplier": 5})
    seed = TeamSeed(panel.factory, panel.clock, panel.brand_id)
    nominal = role_max_durations()["copywriter"]
    seed.seed_run("copywriter", "running", panel.clock.now() - timedelta(seconds=2 * nominal))
    response = panel.get("/oficina/estado")
    assert response.status_code == 200
    writer = next(a for a in response.json()["agentes"] if a["papel"] == "copywriter")
    assert writer["estado"] == "a_trabalhar"
    panel.clock.advance(timedelta(seconds=4 * nominal))
    writer = next(
        a for a in panel.get("/oficina/estado").json()["agentes"] if a["papel"] == "copywriter"
    )
    assert writer["estado"] == "desatualizado"
