from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.compare_reports import compare_report_data
from tlsci.util import canonical_json, read_json, sha256_bytes


RUNNERS = ("one", "two", "three")
CHECKPOINTS = (0, 1, 8, 63, 64, 65, 256, 1024, 4096)
PLATEAU = {"64", "65", "256", "1024", "4096"}
HEX_256 = re.compile(r"^[0-9a-f]{64}$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")


class HostedEvidenceError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HostedEvidenceError(message)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HostedEvidenceError(f"cannot read valid JSON at {path}: {exc}") from exc
    _require(isinstance(value, dict), f"expected JSON object at {path}")
    return value


def _validate_bounded(run: Any, label: str) -> dict[str, Any]:
    _require(isinstance(run, dict), f"{label}: bounded run summary is missing")
    _require(run.get("technical_mvp_bounded_go") == "go", f"{label}: bounded acceptance is not GO")
    _require(run.get("coverage_complete") is True, f"{label}: checkpoint coverage is incomplete")
    _require(run.get("two_pass_reproducibility") is True, f"{label}: two measurement passes differ")
    _require(run.get("transition_count") == 4096, f"{label}: expected exactly 4096 transitions")
    _require(run.get("checkpoint_count") == 9, f"{label}: expected exactly nine checkpoints")
    _require(run.get("issues") == [], f"{label}: bounded acceptance reports issues")
    for field in ("manifest_sha256", "state_trace_sha256"):
        _require(
            isinstance(run.get(field), str) and HEX_256.fullmatch(run[field]) is not None,
            f"{label}: {field} is missing or malformed",
        )

    checkpoints = run.get("checkpoints")
    _require(isinstance(checkpoints, list) and len(checkpoints) == len(CHECKPOINTS), f"{label}: checkpoint list is incomplete")
    _require(
        [item.get("completed_calls") for item in checkpoints if isinstance(item, dict)] == list(CHECKPOINTS),
        f"{label}: checkpoint call counts differ from the locked protocol",
    )
    expected_cardinality = [0, 1, 8, 63, 64, 64, 64, 64, 64]
    _require(
        [item.get("storage_cardinality") for item in checkpoints if isinstance(item, dict)] == expected_cardinality,
        f"{label}: reached-state cardinalities differ from the locked protocol",
    )
    _require(
        all(isinstance(item.get("minimum_successful_gas_budget"), int) and item["minimum_successful_gas_budget"] > 0 for item in checkpoints),
        f"{label}: checkpoint gas budget is missing",
    )

    direct = run.get("direct_replays")
    _require(isinstance(direct, list) and len(direct) == len(CHECKPOINTS), f"{label}: direct boundary replays are incomplete")
    _require(
        [item.get("completed_calls") for item in direct if isinstance(item, dict)] == list(CHECKPOINTS),
        f"{label}: direct replay checkpoint list differs",
    )
    _require(
        all(item.get("minimum_succeeded") is True and item.get("lower_status") == "gas_exhausted" for item in direct),
        f"{label}: direct minimum/minus-one boundary checks failed",
    )
    plateau = run.get("post_saturation_budgets")
    _require(isinstance(plateau, dict) and set(plateau) == PLATEAU, f"{label}: post-saturation checkpoints are incomplete")
    _require(
        all(isinstance(value, int) and value > 0 for value in plateau.values()) and len(set(plateau.values())) == 1,
        f"{label}: post-saturation budgets do not form an exact plateau",
    )
    return {
        "manifest_sha256": run["manifest_sha256"],
        "state_trace_sha256": run["state_trace_sha256"],
        "transition_count": run["transition_count"],
        "checkpoints": checkpoints,
        "post_saturation_budgets": plateau,
        "direct_replays": direct,
    }


def verify_hosted_evidence(
    root: Path,
    *,
    commit_sha: str,
    run_id: str,
    run_attempt: int,
    workflow_ref: str,
    reproduce_result: str,
) -> dict[str, Any]:
    _require(reproduce_result == "success", f"reproduction matrix job result is {reproduce_result!r}")
    _require(HEX_40.fullmatch(commit_sha) is not None, "expected a full lowercase 40-character commit SHA")
    _require(run_id.isdigit() and run_attempt > 0, "workflow run identity is malformed")

    runner_summaries: dict[str, Any] = {}
    family_reports: dict[str, list[Path]] = {"run-a": [], "run-b": [], "growth-v2": []}
    for runner_id in RUNNERS:
        runner_root = root / f"bounded-sequence-v1-{runner_id}"
        metadata_path = runner_root / "metadata.json"
        metadata = _read(metadata_path)
        _require(metadata.get("schema_version") == 1, f"{runner_id}: unsupported metadata schema")
        _require(metadata.get("runner_id") == runner_id, f"{runner_id}: metadata runner identity differs")
        _require(metadata.get("commit_sha") == commit_sha, f"{runner_id}: source commit differs")
        _require(metadata.get("workflow_run_id") == run_id, f"{runner_id}: workflow run id differs")
        _require(metadata.get("workflow_run_attempt") == run_attempt, f"{runner_id}: workflow attempt differs")
        _require(metadata.get("workflow_ref") == workflow_ref, f"{runner_id}: workflow reference differs")
        _require(metadata.get("event_name") == "workflow_dispatch", f"{runner_id}: evidence did not come from manual dispatch")

        go_path = runner_root / "technical-mvp-go-v3.json"
        go = _read(go_path)
        _require(go.get("experiment_id") == "technical-mvp-go-v3", f"{runner_id}: unexpected technical decision")
        _require(go.get("technical_mvp_go") == "go" and go.get("open_issues") == [], f"{runner_id}: technical decision is not a clean GO")
        bounded = go.get("bounded_lifetime_sequence_v1")
        _require(isinstance(bounded, dict), f"{runner_id}: bounded decision block is missing")
        _require(bounded.get("fresh_runs_match") is True, f"{runner_id}: two fresh bounded runs differ")
        _require(bounded.get("manifest_hash_match") is True, f"{runner_id}: bounded manifests differ")
        _require(bounded.get("full_transition_trace_match") is True, f"{runner_id}: local transition traces differ")
        _require(bounded.get("checkpoint_and_gas_match") is True, f"{runner_id}: checkpoints or gas budgets differ")
        _require(bounded.get("direct_boundary_replays_match") is True, f"{runner_id}: direct boundary replays differ")

        growth = go.get("growth_v2")
        _require(isinstance(growth, dict), f"{runner_id}: growth controls are missing")
        for field in ("coverage_complete", "two_pass_reproducibility", "positive_growth_controls_pass", "unrelated_ledger_negative_control_pass"):
            _require(growth.get(field) is True, f"{runner_id}: growth control {field} failed")
        _require(
            growth.get("historical_acceptance_result") == "measured_technical_criteria_not_met",
            f"{runner_id}: historical growth-v2 bounded result was not preserved",
        )

        run_summary = {
            "run_a": _validate_bounded(bounded.get("run_a"), f"{runner_id}/run-a"),
            "run_b": _validate_bounded(bounded.get("run_b"), f"{runner_id}/run-b"),
        }
        _require(run_summary["run_a"] == run_summary["run_b"], f"{runner_id}: run-a and run-b evidence differ")
        runner_summaries[runner_id] = {
            "metadata": metadata,
            "technical_mvp_go": go["technical_mvp_go"],
            "run_a": run_summary["run_a"],
            "run_b": run_summary["run_b"],
            "growth_controls": growth,
        }

        required_files = (
            "run-a/corpus/manifest.json",
            "run-a/corpus/state-trace.json",
            "run-a/measurement/report.json",
            "run-a/bounded-acceptance.json",
            "run-b/corpus/manifest.json",
            "run-b/corpus/state-trace.json",
            "run-b/measurement/report.json",
            "run-b/bounded-acceptance.json",
            "growth-v2/report.json",
            "growth-v2/acceptance.json",
        )
        for relative in required_files:
            path = runner_root / relative
            _require(path.is_file() and path.stat().st_size > 0, f"{runner_id}: required evidence file is missing: {relative}")
        family_reports["run-a"].append(runner_root / "run-a/measurement/report.json")
        family_reports["run-b"].append(runner_root / "run-b/measurement/report.json")
        family_reports["growth-v2"].append(runner_root / "growth-v2/report.json")

    _require(runner_summaries["one"]["run_a"] == runner_summaries["two"]["run_a"] == runner_summaries["three"]["run_a"], "clean-runner bounded evidence differs")
    comparisons: dict[str, str] = {}
    for family, paths in family_reports.items():
        equal, digest = compare_report_data(paths)
        _require(equal, f"clean-runner {family} reports are not byte-normalized equivalents")
        comparisons[family] = digest

    unsigned = {
        "schema_version": 1,
        "source_commit": commit_sha,
        "workflow_ref": workflow_ref,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "runner_ids": list(RUNNERS),
        "reproduce_job_result": reproduce_result,
        "runner_evidence": runner_summaries,
        "normalized_report_sha256": comparisons,
        "hosted_technical_go": "go",
        "claim_boundary": (
            "three clean hosted reproductions of the named fixtures and pinned mockup only; "
            "not independent-user review, user demand, payment intent, grant acceptance, or revenue"
        ),
    }
    unsigned["evidence_sha256"] = sha256_bytes(canonical_json(unsigned).encode("utf-8"))
    return unsigned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed verification of three clean hosted evidence runners")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--reproduce-result", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_hosted_evidence(
            args.root,
            commit_sha=args.commit,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_ref=args.workflow_ref,
            reproduce_result=args.reproduce_result,
        )
        status = 0
    except (OSError, json.JSONDecodeError, HostedEvidenceError, ValueError, KeyError, TypeError) as exc:
        result = {
            "schema_version": 1,
            "hosted_technical_go": "invalid",
            "error": f"{type(exc).__name__}: {exc}",
        }
        status = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
