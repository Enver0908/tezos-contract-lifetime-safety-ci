from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.bounded_sequence import PROTOCOL_PATH, save_new_json, verify_bounded_artifacts
from tools.verify_growth_report import PROJECT, verify as verify_growth_v2
from tlsci.util import canonical_json, read_json


def verify_technical_go_v3(
    growth_report: Path,
    run_a_corpus: Path,
    run_a_report: Path,
    run_b_corpus: Path,
    run_b_report: Path,
    *,
    runtime_path: Path | None = None,
    protocol_path: Path = PROTOCOL_PATH,
    run_a_acceptance: Path | None = None,
    run_b_acceptance: Path | None = None,
) -> dict[str, Any]:
    runtime_path = runtime_path or PROJECT / "runtime.lock.json"
    # Recompute growth-v2 acceptance, including its direct Octez boundary replays.
    # The historical bounded comparison is retained in the output but is not used
    # as evidence about a sequentially reached bounded state.
    growth = verify_growth_v2(growth_report.resolve(), runtime_path.resolve())
    growth_gates = growth.get("growth_acceptance")
    if not isinstance(growth_gates, dict):
        raise ValueError("fresh growth-v2 acceptance has no growth-control evidence")
    positive = growth_gates.get("unbounded_and_both_real_contract_growth_pass") is True
    negative = growth_gates.get("unrelated_ledger_negative_control_does_not_cross_growth_signal") is True
    legacy_checks = growth_gates.get("comparisons")
    legacy_bounded = next(
        (
            item for item in legacy_checks
            if isinstance(item, dict) and item.get("scenario_id") == "bounded_last64_append"
        ),
        None,
    ) if isinstance(legacy_checks, list) else None
    if (
        growth.get("coverage_complete") is not True
        or growth.get("two_pass_reproducibility") is not True
        or growth.get("semantic_postconditions") != "pass"
        or not positive
        or not negative
        or not isinstance(legacy_bounded, dict)
        or legacy_bounded.get("from_size") != 64
        or legacy_bounded.get("to_size") != 4096
    ):
        raise ValueError("growth-v2 non-bounded controls or integrity checks did not pass")

    bounded_a = verify_bounded_artifacts(
        run_a_corpus,
        run_a_report,
        protocol_path=protocol_path,
        runtime_path=runtime_path,
    )
    bounded_b = verify_bounded_artifacts(
        run_b_corpus,
        run_b_report,
        protocol_path=protocol_path,
        runtime_path=runtime_path,
    )
    for output_path, acceptance in (
        (run_a_acceptance, bounded_a),
        (run_b_acceptance, bounded_b),
    ):
        if output_path is not None:
            output_path = output_path.resolve()
            if output_path.exists():
                if canonical_json(read_json(output_path)) != canonical_json(acceptance):
                    raise ValueError(f"existing bounded acceptance differs from recomputed evidence: {output_path}")
            else:
                save_new_json(output_path, acceptance)

    same_corpus = bounded_a.get("manifest_sha256") == bounded_b.get("manifest_sha256")
    same_trace = bounded_a.get("state_trace_sha256") == bounded_b.get("state_trace_sha256")
    same_checkpoints = bounded_a.get("checkpoints") == bounded_b.get("checkpoints")
    same_plateau = bounded_a.get("post_saturation_budgets") == bounded_b.get("post_saturation_budgets")
    same_replays = bounded_a.get("direct_replays") == bounded_b.get("direct_replays")
    fresh_runs_match = same_corpus and same_trace and same_checkpoints and same_plateau and same_replays
    bounded_pass = (
        bounded_a.get("technical_mvp_bounded_go") == "go"
        and bounded_b.get("technical_mvp_bounded_go") == "go"
        and fresh_runs_match
    )
    result = {
        "schema_version": 1,
        "experiment_id": "technical-mvp-go-v3",
        "technical_mvp_go": "go" if bounded_pass else "no-go",
        "result": "pass" if bounded_pass else "measured_technical_criteria_not_met",
        "growth_v2": {
            "coverage_complete": growth["coverage_complete"],
            "two_pass_reproducibility": growth["two_pass_reproducibility"],
            "positive_growth_controls_pass": positive,
            "unrelated_ledger_negative_control_pass": negative,
            "historical_bounded_constructed_state_comparison": legacy_bounded,
            "historical_acceptance_result": growth["result"],
        },
        "bounded_lifetime_sequence_v1": {
            "run_a": bounded_a,
            "run_b": bounded_b,
            "fresh_runs_match": fresh_runs_match,
            "manifest_hash_match": same_corpus,
            "full_transition_trace_match": same_trace,
            "checkpoint_and_gas_match": same_checkpoints and same_plateau,
            "direct_boundary_replays_match": same_replays,
        },
        "open_issues": (
            [] if bounded_pass else [
                "the bounded lifetime acceptance rule failed, or the two fresh local runs differ"
            ]
        ),
        "claim_boundary": (
            "technical evidence for the named fixtures and pinned local mockup only; "
            "does not establish user harm, demand, payment intent, grant acceptance, or revenue"
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine fresh growth-v2 and bounded-sequence-v1 evidence")
    parser.add_argument("--growth-report", type=Path, required=True)
    parser.add_argument("--run-a-corpus", type=Path, required=True)
    parser.add_argument("--run-a-report", type=Path, required=True)
    parser.add_argument("--run-a-acceptance", type=Path, required=True)
    parser.add_argument("--run-b-corpus", type=Path, required=True)
    parser.add_argument("--run-b-report", type=Path, required=True)
    parser.add_argument("--run-b-acceptance", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=PROJECT / "runtime.lock.json")
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_technical_go_v3(
            args.growth_report,
            args.run_a_corpus,
            args.run_a_report,
            args.run_b_corpus,
            args.run_b_report,
            runtime_path=args.runtime,
            protocol_path=args.protocol,
            run_a_acceptance=args.run_a_acceptance,
            run_b_acceptance=args.run_b_acceptance,
        )
        save_new_json(args.output, result)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"result": "invalid", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["technical_mvp_go"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
