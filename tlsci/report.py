from __future__ import annotations

import os
from pathlib import Path
import tempfile
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
        "- Gas metric: minimum successful integer gas budget (not exact gas consumed)",
        f"- Exit code: `{exit_code}`",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        "",
        "## Measurements",
        "",
        "`Storage bytes` counts canonical JSON bytes for the storage fixture only; it is not on-chain storage size.",
        "`Gas cap` is the binary-search ceiling. A protocol ceiling is distinct from a project-policy threshold.",
        "",
        "| Scenario | Size | Status | Cap kind | Minimum successful budget | Gas cap | Storage bytes |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for item in report.measurements:
        lines.append(
            f"| `{item.scenario_id}` | {item.size} | `{item.status}` | `{item.cap_kind}` | "
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
    paths = [json_path, markdown_path] + ([html_path] if html_path is not None else [])
    resolved_parents = {path.parent.resolve() for path in paths}
    if len(resolved_parents) != 1:
        raise ValueError("all report files must share one output directory")
    if any(path.exists() for path in paths):
        raise FileExistsError("refusing to overwrite an existing report artifact")

    data: dict[str, Any] = report.to_dict()
    data["exit_code"] = exit_code
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tlsci-report-", dir=json_path.parent) as temporary:
        temporary_path = Path(temporary)
        staged = [
            temporary_path / json_path.name,
            temporary_path / markdown_path.name,
        ]
        write_json(staged[0], data)
        staged[1].write_text(markdown_report(report, exit_code), encoding="utf-8")
        if html_path is not None:
            staged.append(temporary_path / html_path.name)
            staged[-1].write_text(html_report(report, exit_code), encoding="utf-8")
        for staged_path, output_path in zip(staged, paths):
            if output_path.exists():
                raise FileExistsError(f"refusing to overwrite report artifact: {output_path}")
            os.replace(staged_path, output_path)
