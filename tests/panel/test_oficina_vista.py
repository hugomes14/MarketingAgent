"""Vista da oficina no servidor: acessivel, so leitura e com ligacoes existentes."""

import re
from html.parser import HTMLParser

import pytest
from team_seed import TeamSeed

from marketing_agent.oficina.config import ESTILO_REFERENCIA
from marketing_agent.oficina.papeis import PAPEIS


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def with_class(self, name):
        return [(t, a) for t, a in self.tags if name in a.get("class", "").split()]


@pytest.fixture
def office(make_panel):
    panel = make_panel([])
    return panel, TeamSeed(panel.factory, panel.clock, panel.brand_id)


def test_oficina_page_lists_four_avatars_with_text_state_and_pending(office):
    panel, _ = office
    response = panel.get("/")
    assert response.status_code == 200
    html = response.text
    avatars = Elements(html).with_class("avatar")
    assert [a["data-papel"] for _, a in avatars] == list(PAPEIS)
    assert len(Elements(html).with_class("estado-texto")) >= 4
    assert html.count('class="avatar-cara"') == 4
    assert 'aria-label="Equipa"' in html and 'id="painel-trabalho"' in html
    assert 'id="oficina-cena" hidden' in html
    assert 'aria-label="Oficina 3D da equipa"' in html
    assert "Sem trabalho" in html and "Pendências" in html


def test_avatar_links_work_without_js(office):
    panel, _ = office
    for role in PAPEIS:
        response = panel.get(f"/?papel={role}")
        assert response.status_code == 200
        assert f'id="agente-trabalho" data-papel="{role}"' in response.text
        selected = [
            a["data-papel"]
            for _, a in Elements(response.text).with_class("avatar")
            if a.get("aria-current") == "true"
        ]
        assert selected == [role]
    assert panel.get("/?papel=gestor").status_code == 404


@pytest.mark.parametrize("role", PAPEIS)
def test_agent_partial_per_role_links_existing_pages(office, role):
    panel, _ = office
    response = panel.get(f"/oficina/agente/{role}")
    assert response.status_code == 200 and "<html" not in response.text
    links = [a["href"] for t, a in Elements(response.text).tags if t == "a"]
    assert links
    for url in links:
        assert panel.get(url.split("#")[0]).status_code == 200, url
    if role == "diretor":
        assert "Entrevistas (a pedido)" in response.text
    if role == "designer":
        assert "/biblioteca" in links


def test_agent_partial_unknown_role_404(office):
    assert office[0].get("/oficina/agente/gestor").status_code == 404


def test_no_new_actions(office):
    panel, _ = office
    for path in ["/", *(f"/oficina/agente/{p}" for p in PAPEIS)]:
        html = panel.get(path).text
        assert not re.search(r'<form\b|hx-post=|method="post"', html)
        assert panel.post(path).status_code == 405
    assert panel.llm.requests == []


def test_same_package_from_several_avatars(office):
    panel, seed = office
    post = seed.seed_post(review_rounds=2)
    asset = seed.seed_asset()
    seed.seed_revision(post.id, verdict="rever", final_asset_id=asset.id)
    for role in ("copywriter", "designer", "revisor"):
        html = panel.get(f"/oficina/agente/{role}").text
        assert f"/lote/2026-W46#post-{post.id}" in html


def test_weekly_review_button_count_and_url(office):
    panel, seed = office
    seed.seed_post(slot_key="2026-W45-1")
    seed.seed_post(slot_key="2026-W45-2")
    seed.seed_post(slot_key="2026-W46-1")
    html = panel.get("/").text
    link = Elements(html).with_class("revisao-semanal")[0][1]
    assert link["href"] == "/lote/2026-W45"
    assert "Revisão semanal" in html and "<span>2</span>" in html


def test_model_text_escaped_in_partial(office):
    panel, seed = office
    post = seed.seed_post("drafting", tema="<script>alert(1)</script>", last_error="<b>falha</b>")
    seed.seed_job("dead", post.id)
    html = panel.get("/oficina/agente/copywriter").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<b>falha</b>" not in html and "&lt;b&gt;falha&lt;/b&gt;" in html


def test_state_has_text_and_icon_never_color_only(office):
    panel, _ = office
    elements = Elements(panel.get("/").text)
    assert len(elements.with_class("estado-icone")) >= 4
    assert all(a.get("aria-hidden") == "true" for _, a in elements.with_class("estado-icone"))
    assert len(elements.with_class("estado-texto")) >= 4


def test_avatar_data_attributes_from_config(office):
    panel, _ = office
    config = panel.app.state.panel.oficina
    for _, avatar in Elements(panel.get("/").text).with_class("avatar"):
        person = config.personagens[avatar["data-papel"]]
        assert avatar["data-cabelo"] == person.cabelo
        assert avatar["data-roupa"] == person.roupa
        assert avatar["data-estilo"] == ESTILO_REFERENCIA[person.estilo]


def test_root_is_oficina(office):
    panel, _ = office
    response = panel.get("/")
    assert response.status_code == 200
    html = response.text
    assert "<h1>Oficina da equipa</h1>" in html and 'id="oficina"' in html
    assert [a["data-papel"] for _, a in Elements(html).with_class("avatar")] == list(PAPEIS)
    assert 'id="painel-trabalho"' in html and "Revisão semanal" in html
    assert "Estado do mês" not in html  # o orcamento vive agora em /resumo
    assert panel.get("/?papel=revisor").text.count('id="agente-trabalho" data-papel="revisor"') == 1
    assert panel.get("/?papel=gestor").status_code == 404


def test_resumo_has_previous_home_content(office):
    panel, _ = office
    response = panel.get("/resumo")
    assert response.status_code == 200
    html = response.text
    for section in ("orcamento", "pendentes", "por-rever", "plano-semanal", "entrevistas"):
        assert f'id="{section}"' in html, section
    assert "Estado do mês" in html and "Aguarda a sua decisão" in html
    assert 'action="/entrevista"' in html and "Nova entrevista" in html
    assert 'id="oficina"' not in html


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ("", "/"),
        ("?papel=copywriter", "/?papel=copywriter"),
        ("?papel=gestor", "/"),  # papel desconhecido nao e propagado
        ("?papel=revisor&medir=1", "/?papel=revisor&medir=1"),
        ("?medir=1", "/?medir=1"),
        ("?medir=2&papel=%3Cx%3E", "/"),
    ],
)
def test_oficina_alias_redirects_to_root(office, query, target):
    panel, _ = office
    response = panel.client.get("/oficina" + query, headers=panel.headers(), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == target
    assert panel.client.get("/oficina", follow_redirects=False).status_code == 401


def test_nav_links_oficina_and_resumo(office):
    panel, _ = office
    for path in ("/", "/resumo", "/lote/2026-W46"):
        html = panel.get(path).text
        nav = html[html.index('<nav aria-label="Principal">') : html.index("</nav>")]
        assert '<a href="/">Oficina</a>' in nav and '<a href="/resumo">Resumo</a>' in nav
        assert "Início" not in nav
        for label in ("Lote semanal", "Base de conhecimento", "Biblioteca", "Kit de marca"):
            assert label in nav


def test_director_panel_links_interviews_on_request(office):
    panel, _ = office
    html = panel.get("/oficina/agente/diretor").text
    assert '<a href="/resumo#entrevistas">Entrevistas (a pedido)</a>' in html
