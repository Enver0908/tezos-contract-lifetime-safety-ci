from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from tlsci.util import canonical_json, read_json, sha256_bytes


class ReportComparisonError(ValueError):
    pass


def comparable_report(data: Any, path: Path) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") not in {2, 3}:
        raise ReportComparisonError(f"{path}: expected a schema-v2 or schema-v3 report object")
    if data.get("exit_code") not in {0, 1} or data.get("errors") != []:
        raise ReportComparisonError(f"{path}: report did not finish cleanly")
    measurements = data.get("measurements")
    decisions = data.get("decisions")
    if not isinstance(measurements, list) or not measurements:
        raise ReportComparisonError(f"{path}: report has no measurements")
    if not isinstance(decisions, list) or not decisions:
        raise ReportComparisonError(f"{path}: report has no policy decisions")
    provenance = data.get("provenance")
    runtime = data.get("runtime")
    if not isinstance(provenance, dict) or not isinstance(runtime, dict):
        raise ReportComparisonError(f"{path}: provenance or runtime is malformed")
    manifest_data = provenance.get("manifest_data")
    reproducibility = provenance.get("reproducibility")
    if not isinstance(manifest_data, dict) or not isinstance(reproducibility, dict):
        raise ReportComparisonError(f"{path}: manifest or reproducibility metadata is missing")
    if data.get("schema_version") == 3:
        coverage = data.get("coverage")
        if not isinstance(coverage, dict) or coverage.get("complete") is not True:
            raise ReportComparisonError(f"{path}: schema-v3 case coverage is incomplete")
        expected = coverage.get("expected_cases")
        observed = coverage.get("observed_cases")
        if (
            not isinstance(expected, list)
            or not isinstance(observed, list)
            or expected != observed
            or any(coverage.get(key) != [] for key in ("missing_cases", "unexpected_cases", "duplicate_cases"))
        ):
            raise ReportComparisonError(f"{path}: case coverage lists are inconsistent")
        if manifest_data.get("schema_version") == 1:
            if data.get("exit_code") != 0:
                raise ReportComparisonError(f"{path}: legacy manifest report must exit zero")
            if any(
                not isinstance(item, dict)
                or item.get("status") != "success"
                or not isinstance(item.get("minimum_successful_gas_budget"), int)
                or item["minimum_successful_gas_budget"] < 1
                for item in measurements
            ):
                raise ReportComparisonError(f"{path}: legacy manifest report has a failed measurement")
            if any(not isinstance(item, dict) or item.get("decision") != "pass" for item in decisions):
                raise ReportComparisonError(f"{path}: legacy manifest report has a non-pass decision")
            case_expectations = None
        else:
            case_expectations = {
            f"{scenario.get('id')}:{case.get('size')}": case.get("expected_status", "success")
            for scenario in manifest_data.get("scenarios", [])
            if isinstance(scenario, dict)
            for case in scenario.get("cases", [])
            if isinstance(case, dict)
            }
        if case_expectations is not None:
            by_key = {
                f"{item.get('scenario_id')}:{item.get('size')}": item
                for item in measurements if isinstance(item, dict)
            }
            decision_by_key = {
                f"{item.get('scenario_id')}:{item.get('size')}": item
                for item in decisions if isinstance(item, dict)
            }
            if set(by_key) != set(case_expectations) or set(decision_by_key) != set(case_expectations):
                raise ReportComparisonError(f"{path}: manifest, measurement, and decision cases differ")
            protocol_limits = 0
            for key, expected_status in case_expectations.items():
                measurement = by_key[key]
                decision = decision_by_key[key]
                if expected_status == "success":
                    if (
                        measurement.get("status") != "success"
                        or not isinstance(measurement.get("minimum_successful_gas_budget"), int)
                        or measurement["minimum_successful_gas_budget"] < 1
                        or decision.get("decision") not in {"pass", "violation"}
                    ):
                        raise ReportComparisonError(f"{path}: unexpected measurement result for {key}")
                elif expected_status == "protocol_limit_exceeded":
                    protocol_limits += 1
                    if (
                        measurement.get("status") != "budget_exceeded"
                        or measurement.get("cap_kind") != "protocol"
                        or decision.get("decision") != "violation"
                        or "PROTOCOL_LIMIT_EXCEEDED" not in decision.get("rule_ids", [])
                    ):
                        raise ReportComparisonError(f"{path}: expected protocol-limit result is missing for {key}")
                else:
                    raise ReportComparisonError(f"{path}: unsupported expected status for {key}")
            expected_exit = 1 if protocol_limits or any(
                item.get("decision") == "violation" for item in decisions if isinstance(item, dict)
            ) else 0
            if data.get("exit_code") != expected_exit:
                raise ReportComparisonError(f"{path}: exit code does not match declared outcomes")
    else:
        if data.get("exit_code") != 0:
            raise ReportComparisonError(f"{path}: legacy report must have exit code zero")
        if any(
            not isinstance(item, dict)
            or item.get("status") != "success"
            or not isinstance(item.get("minimum_successful_gas_budget"), int)
            or item["minimum_successful_gas_budget"] < 1
            for item in measurements
        ):
            raise ReportComparisonError(f"{path}: report contains a non-successful measurement")
        if any(not isinstance(item, dict) or item.get("decision") != "pass" for item in decisions):
            raise ReportComparisonError(f"{path}: report contains a non-pass policy decision")
    if (
        reproducibility.get("required") is not True
        or reproducibility.get("passed") is not True
        or reproducibility.get("mismatches") != []
        or reproducibility.get("runs_per_scenario") != 2
    ):
        raise ReportComparisonError(f"{path}: required within-run reproducibility did not pass")

    # Exclude the generation time and runner-specific absolute manifest path.
    return {
        "schema_version": data.get("schema_version"),
        "runtime": runtime,
        "manifest_sha256": data.get("manifest_sha256"),
        "manifest_data": manifest_data,
        "measurements": measurements,
        "decisions": decisions,
        "measurement_limit": provenance.get("measurement_limit"),
        "gas_metric": provenance.get("gas_metric"),
        "coverage": data.get("coverage"),
        "exit_code": data.get("exit_code"),
        "reproducibility": {
            "required": True,
            "runs_per_scenario": 2,
            "passed": True,
            "mismatches": [],
        },
    }


def compare_report_data(paths: list[Path]) -> tuple[bool, str]:
    if len(paths) < 2:
        raise ReportComparisonError("at least two clean-runner reports are required")
    normalized = [comparable_report(read_json(path), path) for path in paths]
    signatures = [sha256_bytes(canonical_json(item).encode("utf-8")) for item in normalized]
    return len(set(signatures)) == 1, signatures[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare normalized Tezos CI evidence reports")
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        equal, digest = compare_report_data(args.reports)
    except (OSError, json.JSONDecodeError, ReportComparisonError, ValueError) as exc:
        print(json.dumps({"comparable": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"comparable": True, "identical": equal, "sha256": digest}, ensure_ascii=False))
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
