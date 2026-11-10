"""Rotulos do painel para valores de dominio."""

from jinja2 import Environment

from marketing_agent.db.models import REVISION_ORIGINS
from marketing_agent.panel.labels import install_label_filters


def test_every_revision_origin_has_a_portuguese_label() -> None:
    environment = Environment()
    install_label_filters(environment)
    label = environment.filters["revision_origin_label"]
    assert label("upload") == "upload do responsável"
    # Uma origem nova no modelo sem rotulo apareceria em bruto no cartao do post.
    assert all(label(origin) != origin for origin in REVISION_ORIGINS)
