from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tlsci.growth import resolve_case
from tlsci.models import Measurement
from tlsci.octez import OctezRunner
from tlsci.runtime import load_runtime
from tlsci.util import canonical_json, read_json, sha256_bytes, sha256_json, write_json
from tlsci.validation import load_manifest


PROJECT = Path(__file__).resolve().parents[1]
DIRECT_CASES = {
    ("unbounded_list_append", 1),
    ("unbounded_list_append", 4096),
    ("bounded_last64_append", 1),
    ("bounded_last64_append", 4096),
    ("tzsafe_sign_proposal", 1),
    ("tzsafe_sign_proposal", 256),
    ("fa2_transfer_batch", 1),
    ("fa2_transfer_batch", 256),
    ("fa2_unrelated_ledger_negative_control", 0),
    ("fa2_unrelated_ledger_negative_control", 4096),
}


def _apply_diff(maps: dict[str, list[Any]], lazy_diff: Any) -> None:
    if lazy_diff is None:
        return
    if not isinstance(lazy_diff, list):
        raise ValueError("semantic output lazy_storage_diff is not a list")
    for item in lazy_diff:
        if not isinstance(item, dict) or item.get("kind") != "big_map":
            continue
        map_id = str(item.get("id"))
        if map_id not in maps:
            raise ValueError(f"semantic output updates unknown big_map {map_id}")
        change = item.get("diff")
        if not isinstance(change, dict) or change.get("action") != "update":
            raise ValueError(f"semantic output uses unsupported big_map action for {map_id}")
        for update in change.get("updates", []):
            if not isinstance(update, dict) or "key" not in update:
                raise ValueError(f"semantic output big_map update lacks key for {map_id}")
            key_hash = canonical_json(update["key"])
            maps[map_id][:] = [
                row for row in maps[map_id]
                if canonical_json(row["args"][0]) != key_hash
            ]
            if update.get("value") is not None:
                maps[map_id].append(
                    {"prim": "Elt", "args": [update["key"], update["value"]]}
                )


def _find_map_value(rows: list[Any], key: Any) -> Any | None:
    wanted = canonical_json(key)
    for row in rows:
        if isinstance(row, dict) and row.get("prim") == "Elt" and canonical_json(row["args"][0]) == wanted:
            return row["args"][1]
    return None


def _assert_semantics(measurement: dict[str, Any], scenario: Any, case: Any, context: Any) -> None:
    output = measurement.get("semantic_output")
    if not isinstance(output, dict):
        raise ValueError(f"{scenario.scenario_id}:{case.size} has no semantic output")
    storage = output.get("storage")
    if output.get("operations") != []:
        raise ValueError(f"{scenario.scenario_id}:{case.size} generated an internal operation")
    size = case.size

    if scenario.scenario_id == "unbounded_list_append":
        if not isinstance(storage, list) or len(storage) != size + 1:
            raise ValueError(f"unbounded list output length is wrong at size {size}")
        if storage != [{"int": "1"}] * (size + 1):
            raise ValueError(f"unbounded list output contents are wrong at size {size}")
    elif scenario.scenario_id == "bounded_last64_append":
        expected = [{"int": "1"}, *[{"int": str(value)} for value in range(size, 0, -1)]][:64]
        if storage != expected:
            raise ValueError(f"bounded-list semantic output is wrong at size {size}")
    elif scenario.scenario_id in {
        "ordinary_map_lookup", "lazy_big_map_lookup", "nested_allowance_lookup",
        "flattened_allowance_big_map_lookup",
    }:
        if (
            not isinstance(storage, dict)
            or storage.get("prim") != "Pair"
            or storage.get("args", [None, None])[1] != {"int": "0" if size == 0 else "1"}
        ):
            raise ValueError(f"lookup semantic result is wrong at size {size}")
    elif scenario.scenario_id == "tzsafe_sign_proposal":
        maps = {
            str(item["id"]): list(item["map_literal"])
            for item in context.extra_big_maps
        }
        _apply_diff(maps, output.get("lazy_storage_diff"))
        proposal_map = next(
            str(item["id"]) for item in context.extra_big_maps if int(item["id"]) == 1_000_001
        )
        proposal = _find_map_value(maps[proposal_map], {"int": "1"})
        if not isinstance(proposal, dict) or len(proposal.get("args", [])) != 5:
            raise ValueError(f"TzSafe proposal state is absent at signature size {size}")
        if len(proposal["args"][1]) != size + 1:
            raise ValueError(f"TzSafe signature count is wrong at prior size {size}")
        if len(output.get("events", [])) != 1 or output["events"][0].get("tag") != "sign_proposal":
            raise ValueError(f"TzSafe sign_proposal event is missing at size {size}")
    elif scenario.scenario_id in {"fa2_transfer_batch", "fa2_unrelated_ledger_negative_control"}:
        maps = {
            str(item["id"]): list(item["map_literal"])
            for item in context.extra_big_maps
        }
        _apply_diff(maps, output.get("lazy_storage_diff"))
        ledger = next(
            str(item["id"]) for item in context.extra_big_maps if int(item["id"]) == 2_000_001
        )
        balances: dict[str, int] = {}
        for row in maps[ledger]:
            address, balance = row["args"]
            balances[address["string"]] = int(balance["int"])
        transfer_count = 1 if scenario.scenario_id.endswith("negative_control") else size
        if balances.get("tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv") != 65_536 - transfer_count:
            raise ValueError(f"FA2 sender balance is wrong at size {size}")
        if balances.get("tz1VSUr8wwNhLAzempoch5d6hLRiTh8Cjcjb") != transfer_count:
            raise ValueError(f"FA2 recipient balance is wrong at size {size}")
        if scenario.scenario_id.endswith("negative_control"):
            unrelated = [
                balance for address, balance in balances.items()
                if address not in {
                    "tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv",
                    "tz1VSUr8wwNhLAzempoch5d6hLRiTh8Cjcjb",
                }
            ]
            if len(unrelated) != size or any(unrelated):
                raise ValueError(f"FA2 negative-control ledger changed at size {size}")
    else:
        raise ValueError(f"unknown growth-v2 scenario {scenario.scenario_id}")


def _direct_replay(
    runner: OctezRunner,
    manifest_path: Path,
    runtime: Any,
    scenario_by_id: dict[str, Any],
    measurements: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for scenario_id, size in sorted(DIRECT_CASES):
        scenario = scenario_by_id[scenario_id]
        item = measurements[(scenario_id, size)]
        if item.get("status") != "success":
            raise ValueError(f"direct replay selection {scenario_id}:{size} was not successful")
        case = next(case for case in scenario.cases if case.size == size)
        resolved = resolve_case(scenario, size, manifest_path.parent, runtime)
        measured_output = item.get("semantic_output")
        minimum = int(item["minimum_successful_gas_budget"])
        at_minimum = runner.run_code(
            manifest_path.parent / scenario.script,
            resolved.storage,
            resolved.input_value,
            resolved.context,
            minimum,
        )
        if not at_minimum.succeeded:
            raise ValueError(f"direct replay failed at recorded minimum for {scenario_id}:{size}")
        actual_output = {
            "status": at_minimum.status,
            "storage": at_minimum.storage,
            "operations": at_minimum.operations,
            "lazy_storage_diff": at_minimum.lazy_storage_diff,
            "events": at_minimum.events,
        }
        expected_output = {"status": "success", **measured_output}
        if sha256_json(actual_output) != sha256_json(expected_output):
            raise ValueError(f"direct Octez semantic output differs at {scenario_id}:{size}")
        lower_boundary = item.get("last_failed_gas_budget")
        lower_status: str | None = None
        if isinstance(lower_boundary, int) and lower_boundary > 0:
            below = runner.run_code(
                manifest_path.parent / scenario.script,
                resolved.storage,
                resolved.input_value,
                resolved.context,
                lower_boundary,
            )
            if below.status != "gas_exhausted":
                raise ValueError(f"direct Octez did not confirm failed lower boundary at {scenario_id}:{size}")
            lower_status = below.status
        results.append(
            {
                "scenario_id": scenario_id,
                "size": size,
                "recorded_minimum_successful_budget": minimum,
                "direct_minimum_status": at_minimum.status,
                "semantic_output_match": True,
                "recorded_last_failed_budget": lower_boundary,
                "direct_lower_boundary_status": lower_status,
            }
        )
    return results


def _growth_checks(measurements: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    def comparison(scenario_id: str, low_size: int, high_size: int) -> dict[str, Any]:
        low = measurements[(scenario_id, low_size)]
        high = measurements[(scenario_id, high_size)]
        if low.get("status") != "success" or high.get("status") != "success":
            return {
                "scenario_id": scenario_id,
                "from_size": low_size,
                "to_size": high_size,
                "passed": False,
                "reason": "both endpoints must be successful",
            }
        old = int(low["minimum_successful_gas_budget"])
        new = int(high["minimum_successful_gas_budget"])
        delta = new - old
        percent = (delta * 100.0 / old) if old else None
        return {
            "scenario_id": scenario_id,
            "from_size": low_size,
            "to_size": high_size,
            "from_minimum_budget": old,
            "to_minimum_budget": new,
            "delta_gas": delta,
            "delta_percent": round(percent, 2) if percent is not None else None,
            "exceeds_both_project_signal_thresholds": delta > 50 and percent is not None and percent > 15,
        }

    checks = [
        comparison("unbounded_list_append", 1, 4096),
        comparison("bounded_last64_append", 64, 4096),
        comparison("tzsafe_sign_proposal", 1, 256),
        comparison("fa2_transfer_batch", 1, 256),
        comparison("fa2_unrelated_ledger_negative_control", 0, 4096),
    ]
    by_id = {item["scenario_id"]: item for item in checks}
    required_positive = (
        "unbounded_list_append",
        "tzsafe_sign_proposal",
        "fa2_transfer_batch",
    )
    positive_pass = all(
        by_id[item].get("exceeds_both_project_signal_thresholds") is True
        for item in required_positive
    )
    bounded_pass = by_id["bounded_last64_append"].get("exceeds_both_project_signal_thresholds") is False
    negative_pass = by_id["fa2_unrelated_ledger_negative_control"].get(
        "exceeds_both_project_signal_thresholds"
    ) is False
    return {
        "project_signal_rule": "delta > 50 gas AND increase > 15%; not a protocol or Foundation rule",
        "comparisons": checks,
        "unbounded_and_both_real_contract_growth_pass": positive_pass,
        "bounded_history_does_not_cross_growth_signal": bounded_pass,
        "unrelated_ledger_negative_control_does_not_cross_growth_signal": negative_pass,
        "passed": positive_pass and bounded_pass and negative_pass,
    }


def verify(report_path: Path, runtime_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    if not isinstance(report, dict) or report.get("schema_version") != 3:
        raise ValueError("growth-v2 acceptance requires a schema-v3 report")
    manifest_path = PROJECT / "fixtures" / "growth-v2" / "manifest.json"
    runtime = load_runtime(runtime_path)
    manifest, scenarios, manifest_hash = load_manifest(manifest_path, runtime)
    if report.get("manifest_sha256") != manifest_hash:
        raise ValueError("report manifest checksum does not match the current corpus")
    for field in ("octez_digest", "protocol", "chain_id", "hard_gas_limit_per_operation"):
        if report.get("runtime", {}).get(field) != runtime.to_dict().get(field):
            raise ValueError(f"report runtime does not match lock: {field}")
    coverage = report.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        raise ValueError("report case coverage is incomplete")
    if any(coverage.get(key) != [] for key in ("missing_cases", "unexpected_cases", "duplicate_cases")):
        raise ValueError("report contains missing, unexpected, or duplicate case coverage")
    rows = report.get("measurements")
    if not isinstance(rows, list) or len(rows) != 61:
        raise ValueError("growth-v2 requires exactly 61 measurements")
    if coverage.get("expected_cases") != coverage.get("observed_cases"):
        raise ValueError("report observed case order differs from manifest case order")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("report provenance is malformed")
    repro = provenance.get("reproducibility")
    if (
        not isinstance(repro, dict)
        or repro.get("required") is not True
        or repro.get("runs_per_scenario") != 2
        or repro.get("passed") is not True
        or repro.get("mismatches") != []
    ):
        raise ValueError("two-pass measurement reproducibility did not pass")
    if provenance.get("measurement_set_sha256") != sha256_json(rows):
        raise ValueError("report measurement-set checksum does not match its rows")

    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    expected: dict[tuple[str, int], str] = {
        (scenario.scenario_id, case.size): case.expected_status
        for scenario in scenarios for case in scenario.cases
    }
    measurement_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("report contains a malformed measurement")
        key = (str(row.get("scenario_id")), int(row.get("size", -1)))
        if key in measurement_by_key:
            raise ValueError(f"duplicate report measurement: {key}")
        if key not in expected:
            raise ValueError(f"unexpected report measurement: {key}")
        measurement_by_key[key] = row
        if row.get("measurement_method") != "integer_budget_search_v3":
            raise ValueError(f"unexpected measurement method for {key}")
        if (
            row.get("gas_cap") != runtime.hard_gas_limit_per_operation
            or row.get("cap_kind") != "protocol"
            or row.get("execution_scope") != "single_script"
        ):
            raise ValueError(f"protocol ceiling or execution scope mismatch for {key}")
        if row.get("internal_operations_executed") is not False:
            raise ValueError(f"report incorrectly claims internal operations executed for {key}")
        actual = (
            "protocol_limit_exceeded"
            if row.get("status") == "budget_exceeded" and row.get("cap_kind") == "protocol"
            else row.get("status")
        )
        if actual != expected[key]:
            raise ValueError(f"declared outcome mismatch for {key}: expected={expected[key]} actual={actual}")
        scenario = scenario_by_id[key[0]]
        case = next(case for case in scenario.cases if case.size == key[1])
        resolved = resolve_case(scenario, key[1], manifest_path.parent, runtime)
        expected_fixture = sha256_json(
            {
                "storage": resolved.storage,
                "input": resolved.input_value,
                "context": resolved.context.to_dict(),
            }
        )
        if row.get("fixture_sha256") != expected_fixture:
            raise ValueError(f"fixture hash mismatch for {key}")
        if row.get("context_sha256") != sha256_json(resolved.context.to_dict()):
            raise ValueError(f"context hash mismatch for {key}")
        if row.get("code_sha256") != sha256_bytes((manifest_path.parent / scenario.script).read_bytes()):
            raise ValueError(f"code hash mismatch for {key}")
        if row.get("input_bytes") != len(canonical_json(resolved.input_value).encode("utf-8")):
            raise ValueError(f"input byte count mismatch for {key}")
        if row.get("state_validity") != case.state_validity:
            raise ValueError(f"state validity label mismatch for {key}")
        if row.get("state_trace_sha256") != resolved.sequence_trace_sha256:
            raise ValueError(f"state trace hash mismatch for {key}")
        if actual == "success":
            _assert_semantics(row, scenario, case, resolved.context)
            expected_semantic_hash = sha256_json({"status": "success", **row["semantic_output"]})
            if row.get("semantic_output_sha256") != expected_semantic_hash:
                raise ValueError(f"semantic output hash mismatch for {key}")
    if set(measurement_by_key) != set(expected):
        raise ValueError("report measurements do not cover every manifest case exactly once")

    protocol_limits = [key for key, value in expected.items() if value == "protocol_limit_exceeded"]
    decisions = report.get("decisions")
    decision_by_key = {
        (str(item.get("scenario_id")), int(item.get("size", -1))): item
        for item in decisions if isinstance(item, dict)
    } if isinstance(decisions, list) else {}
    if set(decision_by_key) != set(expected):
        raise ValueError("report policy decisions do not cover every manifest case")
    for key in protocol_limits:
        decision = decision_by_key[key]
        if decision.get("decision") != "violation" or "PROTOCOL_LIMIT_EXCEEDED" not in decision.get("rule_ids", []):
            raise ValueError(f"protocol limit finding lacks an explicit violation at {key}")
    required_exit = 1 if protocol_limits or any(
        item.get("decision") == "violation" for item in decision_by_key.values()
    ) else 0
    if report.get("exit_code") != required_exit:
        raise ValueError("report exit code is inconsistent with recorded policy outcomes")
    errors = report.get("errors")
    if errors != []:
        raise ValueError("report contains run errors")

    with OctezRunner(runtime, PROJECT, timeout=60) as runner:
        direct = _direct_replay(
            runner,
            manifest_path,
            runtime,
            scenario_by_id,
            measurement_by_key,
        )
    growth = _growth_checks(measurement_by_key)
    go = growth["passed"]
    return {
        "schema_version": 1,
        "result": "pass" if go else "measured_technical_criteria_not_met",
        "technical_mvp_go": "go" if go else "no-go",
        "manifest_sha256": manifest_hash,
        "measurement_count": len(rows),
        "coverage_complete": True,
        "two_pass_reproducibility": True,
        "semantic_postconditions": "pass",
        "expected_protocol_limit_cases": [f"{scenario}:{size}" for scenario, size in protocol_limits],
        "growth_acceptance": growth,
        "direct_octez_replays": direct,
        "claim_boundary": "technical mockup evidence only; not user harm, demand, payment intent, grant acceptance, or revenue",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a complete growth-v2 Octez report")
    parser.add_argument("report", type=Path)
    parser.add_argument("--runtime", type=Path, default=PROJECT / "runtime.lock.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.report.resolve(), args.runtime.resolve())
        if args.output is not None:
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["technical_mvp_go"] == "go" else 1
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(json.dumps({"result": "fail", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
