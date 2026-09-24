from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Iterable

from .models import Measurement, PolicyDecision, RunReport


def _text(value: object) -> str:
    return escape(str(value))


def _chart(measurements: Iterable[Measurement]) -> str:
    grouped: dict[str, list[Measurement]] = defaultdict(list)
    for measurement in measurements:
        if measurement.minimum_successful_gas_budget is not None:
            grouped[measurement.scenario_id].append(measurement)
    if not grouped:
        return "<p class=\"muted\">Çizilecek başarılı ölçüm bulunmuyor.</p>"

    charts: list[str] = []
    for scenario_id, values in sorted(grouped.items()):
        values = sorted(values, key=lambda item: item.size)
        width = 720
        height = 230
        left = 58
        bottom = 38
        top = 20
        right = 20
        plot_width = width - left - right
        plot_height = height - top - bottom
        maximum = max(item.minimum_successful_gas_budget or 0 for item in values) or 1
        x_step = plot_width / max(len(values) - 1, 1)
        points: list[str] = []
        labels: list[str] = []
        for index, item in enumerate(values):
            x = left + (index * x_step if len(values) > 1 else plot_width / 2)
            y = top + plot_height - ((item.minimum_successful_gas_budget or 0) / maximum * plot_height)
            points.append(f"{x:.1f},{y:.1f}")
            labels.append(
                f'<text x="{x:.1f}" y="{height - 12}" text-anchor="middle" class="axis">{_text(item.size)}</text>'
            )
            labels.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="point"><title>{_text(item.minimum_successful_gas_budget)}</title></circle>'
            )
        grid = []
        for fraction in (0, 0.5, 1):
            y = top + plot_height - (fraction * plot_height)
            value = int(maximum * fraction)
            grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" class="grid"/>')
            grid.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{value}</text>')
        charts.append(
            f'<div class="chart"><h3>{_text(scenario_id)}</h3>'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_text(scenario_id)} gas budget chart">'
            f'{"".join(grid)}<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" class="axis-line"/>'
            f'<line x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" class="axis-line"/>'
            f'<polyline points="{" ".join(points)}" class="series"/>{"".join(labels)}</svg></div>'
        )
    return "".join(charts)


def _measurement_rows(measurements: Iterable[Measurement]) -> str:
    rows = []
    for item in measurements:
        budget = item.minimum_successful_gas_budget
        rows.append(
            "<tr>"
            f"<td>{_text(item.scenario_id)}</td>"
            f"<td>{_text(item.size)}</td>"
            f"<td><span class=\"status {escape(item.status)}\">{_text(item.status)}</span></td>"
            f"<td>{_text(budget if budget is not None else '—')}</td>"
            f"<td>{_text(item.gas_cap)}</td>"
            f"<td>{_text(item.storage_bytes if item.storage_bytes is not None else '—')}</td>"
            f"<td>{_text(item.fixture_sha256[:12])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _decision_rows(decisions: Iterable[PolicyDecision]) -> str:
    rows = []
    for item in decisions:
        rows.append(
            "<tr>"
            f"<td>{_text(item.scenario_id)}</td>"
            f"<td>{_text(item.size)}</td>"
            f"<td><span class=\"decision {escape(item.decision)}\">{_text(item.decision)}</span></td>"
            f"<td>{_text(', '.join(item.rule_ids) if item.rule_ids else '—')}</td>"
            f"<td>{_text(item.message)}</td>"
            "</tr>"
        )
    return "".join(rows)


def html_report(report: RunReport, exit_code: int) -> str:
    errors = "".join(f"<li>{_text(error)}</li>" for error in report.errors)
    errors_section = f"<section><h2>Errors</h2><ul>{errors}</ul></section>" if errors else ""
    reproducibility = report.provenance.get("reproducibility", {})
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tezos Contract Lifetime Safety CI report</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
body {{ margin: 0; background: #f5f7fb; color: #182033; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 64px; }}
section, .chart {{ background: white; border: 1px solid #d9dfec; border-radius: 12px; padding: 20px; margin: 18px 0; overflow-x: auto; }}
h1 {{ margin-bottom: 6px; }} h2 {{ margin-top: 0; }} h3 {{ margin-top: 0; }}
.meta, .muted {{ color: #5b667d; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
.card {{ background: white; border: 1px solid #d9dfec; border-radius: 12px; padding: 16px; }}
.card strong {{ display: block; font-size: 1.5rem; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
th, td {{ text-align: left; padding: 9px 8px; border-bottom: 1px solid #e7ebf3; vertical-align: top; }}
th {{ color: #4a5670; font-size: .85rem; }}
.status, .decision {{ border-radius: 999px; padding: 3px 8px; font-size: .82rem; }}
.success, .pass {{ background: #d8f5e5; color: #12653a; }}
.invalid, .violation, .over_hard_limit {{ background: #ffe0e0; color: #8a1c1c; }}
.grid {{ stroke: #e1e6f0; stroke-width: 1; }} .axis-line {{ stroke: #8b96aa; stroke-width: 1; }}
.axis {{ fill: #5b667d; font-size: 11px; }} .series {{ fill: none; stroke: #3167d6; stroke-width: 3; }} .point {{ fill: #3167d6; }}
code {{ word-break: break-all; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #111827; color: #e5e7eb; }} section, .chart, .card {{ background: #1f2937; border-color: #374151; }} .meta, .muted, th, .axis {{ color: #aab4c5; fill: #aab4c5; }} th, td {{ border-color: #374151; }} .grid {{ stroke: #374151; }} .axis-line {{ stroke: #9ca3af; }} }}
</style>
</head>
<body><main>
<h1>Tezos Contract Lifetime Safety CI report</h1>
<p class="meta">Generated {_text(report.generated_at_utc)} · exit code {_text(exit_code)}</p>
<div class="cards">
<div class="card">Runtime<strong>{_text(report.runtime.get('octez_version', 'unknown'))}</strong></div>
<div class="card">Measurements<strong>{_text(len(report.measurements))}</strong></div>
<div class="card">Policy decisions<strong>{_text(len(report.decisions))}</strong></div>
<div class="card">Reproducibility<strong>{_text('pass' if reproducibility.get('passed', True) else 'fail')}</strong></div>
</div>
<section><h2>Scope and provenance</h2>
<p>Protocol: <code>{_text(report.runtime.get('protocol'))}</code></p>
<p>Manifest SHA-256: <code>{_text(report.manifest_sha256)}</code></p>
<p>Measurement: minimum successful integer gas budget; this is not exact consumed gas and does not execute internal operations.</p>
</section>
<section><h2>Gas budget by fixture size</h2><div class="charts">{_chart(report.measurements)}</div></section>
<section><h2>Measurements</h2><table><thead><tr><th>Scenario</th><th>Size</th><th>Status</th><th>Minimum budget</th><th>Gas cap</th><th>Storage bytes</th><th>Fixture hash</th></tr></thead><tbody>{_measurement_rows(report.measurements)}</tbody></table></section>
<section><h2>Policy decisions</h2><table><thead><tr><th>Scenario</th><th>Size</th><th>Decision</th><th>Rules</th><th>Message</th></tr></thead><tbody>{_decision_rows(report.decisions)}</tbody></table></section>
{errors_section}
<section><h2>Limits</h2><p>A passing result is not a security audit, a guarantee of future callability, a user incident, or evidence of demand or revenue. Review the raw JSON for complete provenance.</p></section>
</main></body></html>
"""
