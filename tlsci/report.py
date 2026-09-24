from __future__ import annotations

from pathlib import Path
from typing import Any

from .html_report import html_report
from .models import RunReport
from .util import write_json


def markdown_report(report: RunReport, exit_code: int) -> str:
    lines = [
        "# Tezos Contract Lifetime Safety CI report",
        "",
        f"- Generated: `{report.generated_at_utc}`",
        f"- Runtime protocol: `{report.runtime.get('protocol')}`",
        f"- Octez: `{report.runtime.get('octez_version')}`",
        f"- Exit code: `{exit_code}`",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        "",
        "## Measurements",
        "",
        "| Scenario | Size | Status | Minimum successful budget | Gas cap | Storage bytes |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for item in report.measurements:
        lines.append(
            f"| `{item.scenario_id}` | {item.size} | `{item.status}` | "
            f"{item.minimum_successful_gas_budget if item.minimum_successful_gas_budget is not None else '—'} | "
            f"{item.gas_cap} | {item.storage_bytes if item.storage_bytes is not None else '—'} |"
        )
    lines.extend(["", "## Policy decisions", "", "| Scenario | Size | Decision | Rules | Message |", "|---|---:|---|---|---|"])
    for item in report.decisions:
        lines.append(
            f"| `{item.scenario_id}` | {item.size} | `{item.decision}` | "
            f"{', '.join(item.rule_ids) if item.rule_ids else '—'} | {item.message} |"
        )
    if report.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- `{error}`" for error in report.errors)
    lines.extend(["", "## Measurement limits", "", "- Gas is reported as the minimum successful gas budget found by integer search.", "- This report does not claim exact consumed gas or full internal-operation execution.", "- A successful measurement is not a security guarantee.", ""])
    return "\n".join(lines)


def save_report(
    report: RunReport,
    json_path: Path,
    markdown_path: Path,
    exit_code: int,
    html_path: Path | None = None,
) -> None:
    data: dict[str, Any] = report.to_dict()
    data["exit_code"] = exit_code
    write_json(json_path, data)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_report(report, exit_code), encoding="utf-8")
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_report(report, exit_code), encoding="utf-8")
