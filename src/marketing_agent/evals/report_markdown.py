"""Exportacao Markdown com texto humano escapado e limitacoes sempre presentes."""

import html
import re

from marketing_agent.evals.report_types import EvalReport, PathReport


def _text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", html.escape(value)).replace("\n", " ")


def _path_lines(path: PathReport) -> list[str]:
    latency_mean = path.latency_mean_ms if path.latency_mean_ms is not None else "sem medida"
    latency_max = path.latency_max_ms if path.latency_max_ms is not None else "sem medida"
    row = (
        f"| {path.path} | {path.packages} | {path.failures} "
        f"| {path.cost_mean_usd:.4f} / {path.cost_max_usd:.4f} "
        f"| {path.text_mean_usd:.4f} / {path.text_max_usd:.4f} "
        f"| {path.image_mean_usd:.4f} / {path.image_max_usd:.4f} "
        f"| {latency_mean} / {latency_max} | {path.invalid_outputs} | {path.review_rounds} |"
    )
    errors = "; ".join(f"{kind}: {count}" for kind, count in path.errors.items())
    minutes = "; ".join(
        f"{bucket.replace('2_5', '2–5').replace('mais_5', 'mais de 5')}: {count}"
        for bucket, count in path.edit_minutes.items()
    )
    return [row, f"\nErros ({path.path}): {errors}.", f"Edição ({path.path}), minutos: {minutes}."]


def _measurement_lines(report: EvalReport) -> list[str]:
    decisive = report.wins + report.losses
    interval = (
        f"{report.interval[0]:.4f}–{report.interval[1]:.4f}"
        if report.interval
        else "indisponível (nenhum par decisivo)"
    )
    lines = [
        f"Pares elegíveis: {report.pairs}; excluídos por falha: {report.excluded}.",
        f"Especializado: {report.wins} vitórias, {report.losses} derrotas, {report.ties} empates.",
        f"Intervalo de Wilson a 95 % sobre {decisive} pares decisivos: {interval}.",
        f"Teto estimado: {report.estimate_usd:.4f} USD; "
        f"custo contabilizado: {report.actual_usd:.4f} USD.",
        "",
        "| Percurso | Pacotes | Falhas | Custo médio / máximo USD | Texto médio / máximo "
        "| Imagem média / máxima | Latência média / máxima ms | Saídas inválidas | Voltas |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for path in report.paths:
        lines.extend(_path_lines(path))
    agreement = report.reviewer_agreement
    lines.extend(
        [
            "",
            f"Concordância com o Revisor (qualquer volta): {agreement.matched}/{agreement.total} "
            "erros assinalados pelo responsável no especializado.",
            "A correspondência de tipos é descritiva; não mede verdade factual nem causalidade.",
            "Custos incluem itens falhados e todas as tentativas; "
            "latência sem medida não conta como zero.",
            "Os minutos de edição conservam os intervalos escolhidos, "
            "sem converter 'mais de 5' numa média inventada.",
        ]
    )
    return lines


def render_report(report: EvalReport) -> str:
    """Resultados descritivos, historico de decisoes e pergunta D35 no fim."""
    lines = [
        f"# Relatório de avaliação — {_text(report.set_name)} v{report.set_version}",
        "",
        f"Corrida: {report.run_id}",
        *_measurement_lines(report),
        "",
        "## Limitações",
        "",
        *[f"- {limitation}" for limitation in report.limitations],
        "",
        "## Decisões do responsável",
        "",
    ]
    if not report.decisions:
        lines.append("Ainda não há decisões registadas.")
    for decision in report.decisions:
        lines.append(
            f"- {decision.role}: {decision.decision} — {_text(decision.justificacao)} "
            f"({_text(decision.actor)}, {decision.at.isoformat()})"
        )
    lines.extend(["", report.question, ""])
    return "\n".join(lines)
