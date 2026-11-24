"""Relatorio da avaliacao em Markdown, para guardar em `AgentDATA/docs/` (spec 2C, secao 6)."""

from decimal import Decimal

from marketing_agent.evals.report import EvalReport, PathStats

_EDIT_LABELS = {"0": "0 min", "1": "1 min", "2_5": "2 a 5 min", "mais_5": "mais de 5 min"}


def _interval_text(report: EvalReport) -> str:
    decisive = report.wins + report.losses
    if report.interval is None:
        return "sem pares decisivos: não há intervalo"
    low, high = report.interval
    return (
        f"intervalo de Wilson a 95 % sobre {decisive} pares decisivos: "
        f"{low:.0%} a {high:.0%} de vitórias do especializado"
    )


def _inline(text: str) -> str:
    """Texto livre numa so linha: quebras de linha criariam titulos e itens falsos."""
    return " ".join(text.split())


def _usd(value: Decimal) -> str:
    return f"{value:.4f} USD"


def _path_rows(paths: tuple[PathStats, ...]) -> list[str]:
    header = "| Medida | " + " | ".join(p.path for p in paths) + " |"
    rule = "| --- | " + " | ".join("---:" for _ in paths) + " |"
    rows = [
        ("Pacotes (falhados)", [f"{p.packages} ({p.failed})" for p in paths]),
        ("Custo médio", [_usd(p.cost_mean_usd) for p in paths]),
        ("Custo máximo", [_usd(p.cost_max_usd) for p in paths]),
        ("Custo de imagem (total)", [_usd(p.image_cost_usd) for p in paths]),
        ("Latência média", [f"{p.latency_mean_ms / 1000:.1f} s" for p in paths]),
        ("Latência máxima", [f"{p.latency_max_ms / 1000:.1f} s" for p in paths]),
        ("Saídas inválidas", [str(p.invalid_outputs) for p in paths]),
        ("Voltas do Revisor", [str(p.review_rounds) for p in paths]),
    ]
    for kind in paths[0].errors:
        rows.append((f"Erros: {kind}", [str(p.errors[kind]) for p in paths]))
    for value, label in _EDIT_LABELS.items():
        rows.append((f"Edição: {label}", [str(p.edit_minutes.get(value, 0)) for p in paths]))
    return [header, rule, *(f"| {name} | " + " | ".join(cells) + " |" for name, cells in rows)]


def render_markdown(report: EvalReport, generated_at: str) -> str:
    """Relatorio completo; as limitacoes e a pergunta do D35 estao sempre presentes."""
    agreement = (
        "sem erros marcados no lado especializado"
        if report.reviewer_agreement is None
        else f"{report.reviewer_agreement[0]} de {report.reviewer_agreement[1]} erros marcados "
        "no lado especializado foram também assinalados pelo Revisor"
    )
    lines = [
        f"# Relatório da avaliação cega: {report.set_name} v{report.set_version}",
        "",
        f"- Corrida: `{report.run_id}`",
        f"- Gerado em: `{generated_at}`",
        f"- Pares avaliados: {report.pairs} "
        f"(tempo médio por par: {report.seconds_per_pair_mean} s)",
        "",
        "## Resultado",
        "",
        f"- Especializado: {report.wins} vitórias, {report.losses} derrotas, "
        f"{report.ties} empates.",
        f"- {_interval_text(report)}.",
        f"- Custo: estimativa máxima {_usd(report.estimate_usd)}, real {_usd(report.actual_usd)}.",
        f"- Concordância do Revisor: {agreement}.",
        "",
        "## Por percurso",
        "",
        *_path_rows(report.paths),
        "",
        "## Limitações",
        "",
        *(f"- {item}" for item in report.limitations),
        "",
        "## Decisão (D35)",
        "",
        report.question,
        "",
    ]
    if report.decisions:
        lines += [
            f"- {d.role}: **{d.decision}** ({d.decided_by}, {d.decided_at:%Y-%m-%d}): "
            f"{_inline(d.justificacao)}"
            for d in report.decisions
        ]
    else:
        lines.append("- Ainda sem decisões registadas.")
    if report.notes:
        lines += ["", "## Notas do avaliador", "", *(f"- {_inline(note)}" for note in report.notes)]
    return "\n".join(lines) + "\n"
