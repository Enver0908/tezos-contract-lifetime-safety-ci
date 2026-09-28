from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .baseline import build_baseline, load_baseline, save_baseline
from .capture import CaptureError, capture_big_map_key, capture_contract
from .measure import measure_scenario
from .models import Measurement, RunReport
from .octez import OctezRunner
from .policy import Policy, evaluate_measurements, exit_code_for
from .report import save_report
from .runtime import RuntimeErrorState, doctor, load_runtime
from .util import read_json, sha256_json, utc_now, write_json
from .validation import ManifestError, load_manifest
from .variants import make_controlled_manifest, make_noop_gas_variant


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = PROJECT_ROOT / "runtime.lock.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tlsci", description="Tezos Contract Lifetime Safety CI MVP")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")

    capture = sub.add_parser("capture")
    capture.add_argument("--address", action="append", required=True)
    capture.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "fixtures" / "real")
    capture.add_argument("--rpc", default="https://rpc.tzkt.io/mainnet")
    capture.add_argument("--block", default="head")

    capture_big_map = sub.add_parser("capture-big-map")
    capture_big_map.add_argument("--ptr", type=int, required=True)
    capture_big_map.add_argument("--key", required=True)
    capture_big_map.add_argument("--output", type=Path, required=True)
    capture_big_map.add_argument("--api", default="https://api.tzkt.io")

    variant = sub.add_parser("variant")
    variant.add_argument("--source", type=Path, required=True)
    variant.add_argument("--output", type=Path, required=True)
    variant.add_argument("--repetitions", type=int, required=True)

    controlled_manifest = sub.add_parser("controlled-manifest")
    controlled_manifest.add_argument("--source-manifest", type=Path, required=True)
    controlled_manifest.add_argument("--output-manifest", type=Path, required=True)
    controlled_manifest.add_argument("--repetitions", type=int, required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)

    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--baseline", type=Path)
    run.add_argument("--policy", type=Path, default=PROJECT_ROOT / "policy.json")
    run.add_argument("--timeout", type=int, default=60)
    run.add_argument("--skip-doctor", action="store_true")

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--report", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--baseline", type=Path)
    evaluate.add_argument("--policy", type=Path, required=True)

    baseline = sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    create = baseline_sub.add_parser("create")
    create.add_argument("--report", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--pattern", default="test_*.py")

    sub.add_parser("acceptance")

    return parser


def _load_policy(path: Path) -> Policy:
    if not path.is_file():
        raise FileNotFoundError(f"policy file not found: {path}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"policy file must contain a JSON object: {path}")
    return Policy.from_dict(data)


def _measurement_signature(measurements: list[Measurement]) -> list[dict[str, object]]:
    return [measurement.to_dict() for measurement in measurements]


def _case_keys(scenarios: tuple[object, ...]) -> list[str]:
    return [f"{scenario.scenario_id}:{size}" for scenario in scenarios for size in scenario.sizes]


def _coverage(scenarios: tuple[object, ...], measurements: list[Measurement]) -> dict[str, object]:
    expected = _case_keys(scenarios)
    observed = [f"{item.scenario_id}:{item.size}" for item in measurements]
    expected_set = set(expected)
    observed_set = set(observed)
    return {
        "expected_cases": expected,
        "observed_cases": observed,
        "missing_cases": [key for key in expected if key not in observed_set],
        "unexpected_cases": [key for key in observed if key not in expected_set],
        "duplicate_cases": sorted({key for key in observed if observed.count(key) > 1}),
        "complete": len(expected) == len(observed) and expected_set == observed_set,
    }


def command_run(args: argparse.Namespace) -> int:
    runtime = load_runtime(args.runtime)
    manifest_path = args.manifest.resolve()
    manifest, scenarios, manifest_hash = load_manifest(manifest_path, runtime)
    policy = _load_policy(args.policy)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing to write reports into a non-empty directory: {args.output_dir}")
    baseline = load_baseline(args.baseline) if args.baseline else None
    expected_method = (
        "integer_budget_search_v3"
        if manifest.get("schema_version") == 2
        else "integer_budget_search_v2"
    )
    if manifest.get("schema_version") == 2 and not policy.require_reproducible:
        raise ValueError("schema-v2 manifests require two reproducible measurement passes")
    if baseline is not None:
        expected_runtime = {
            "protocol": runtime.protocol,
            "octez_digest": runtime.octez_digest,
            "chain_id": runtime.chain_id,
            "hard_gas_limit_per_operation": runtime.hard_gas_limit_per_operation,
        }
        mismatches = [
            key for key, value in expected_runtime.items()
            if baseline.runtime.get(key) != value
        ]
        if mismatches:
            raise ValueError(
                "baseline runtime does not match runtime lock: " + ", ".join(mismatches)
            )
        baseline_methods = {
            entry.get("measurement_method") for entry in baseline.scenarios.values()
        }
        allowed_baseline_schema = {3} if manifest.get("schema_version") == 2 else {2, 3}
        if baseline.schema_version not in allowed_baseline_schema:
            raise ValueError("baseline schema is incompatible with this manifest version")
        if baseline_methods != {expected_method}:
                raise ValueError(
                    f"baseline measurement method is incompatible; expected {expected_method}"
                )
    if not args.skip_doctor:
        doctor_result = doctor(runtime)
    else:
        doctor_result = {"skipped": True}
    runner = OctezRunner(runtime, manifest_path.parent, timeout=args.timeout)
    measurements = []
    errors: list[str] = []
    reproducibility: dict[str, object] = {
        "required": policy.require_reproducible,
        "runs_per_scenario": 2 if policy.require_reproducible else 1,
        "passed": True,
        "mismatches": [],
    }
    try:
        with runner:
            for scenario_number, scenario in enumerate(scenarios, start=1):
                try:
                    def progress(item: Measurement, case_number: int, case_count: int) -> None:
                        print(
                            f"pass 1/2 scenario {scenario_number}/{len(scenarios)} "
                            f"{scenario.scenario_id} case {case_number}/{case_count} size={item.size} "
                            f"status={item.status}",
                            flush=True,
                        )

                    first_run = measure_scenario(
                        scenario, runtime, runner, manifest_path.parent, on_case=progress
                    )
                    measurements.extend(first_run)
                    if scenario.manifest_schema_version == 2:
                        declared = {case.size: case.expected_status for case in scenario.cases}
                        mismatches = [
                            f"{item.scenario_id}:{item.size} expected {declared[item.size]}, got {item.status}"
                            for item in first_run
                            if (
                                "protocol_limit_exceeded"
                                if item.status == "budget_exceeded" and item.cap_kind == "protocol"
                                else item.status
                            ) != declared[item.size]
                        ]
                        if mismatches:
                            errors.extend(f"invalid: case expectation mismatch: {item}" for item in mismatches)
                    if policy.require_reproducible:
                        def repeat_progress(item: Measurement, case_number: int, case_count: int) -> None:
                            print(
                                f"pass 2/2 scenario {scenario_number}/{len(scenarios)} "
                                f"{scenario.scenario_id} case {case_number}/{case_count} size={item.size} "
                                f"status={item.status}",
                                flush=True,
                            )

                        second_run = measure_scenario(
                            scenario, runtime, runner, manifest_path.parent, on_case=repeat_progress
                        )
                        if _measurement_signature(first_run) != _measurement_signature(second_run):
                            reproducibility["passed"] = False
                            mismatches = reproducibility["mismatches"]
                            assert isinstance(mismatches, list)
                            mismatches.append(scenario.scenario_id)
                            errors.append(
                                f"invalid: reproducibility mismatch for scenario {scenario.scenario_id}"
                            )
                except (OSError, TypeError, ValueError, KeyError) as exc:
                    errors.append(
                        f"invalid: {scenario.scenario_id}: {type(exc).__name__}: {exc}"
                    )
                except Exception as exc:
                    errors.append(f"{scenario.scenario_id}: {type(exc).__name__}: {exc}")
    except Exception as exc:
        errors.append(f"runner: {type(exc).__name__}: {exc}")
    coverage = _coverage(scenarios, measurements)
    if not coverage["complete"]:
        errors.append("invalid: measured case coverage is incomplete or contains duplicates")
    decisions = evaluate_measurements(measurements, baseline, policy, runtime)
    exit_code = exit_code_for(decisions, errors)
    report = RunReport(
        schema_version=3,
        generated_at_utc=utc_now(),
        runtime={**runtime.to_dict(), "doctor": doctor_result},
        manifest_sha256=manifest_hash,
        measurements=measurements,
        decisions=decisions,
        errors=errors,
        provenance={
            "manifest": str(manifest_path),
            "manifest_data": manifest,
            "measurement_limit": "single_script; internal operations are not executed",
            "gas_metric": "minimum_successful_integer_gas_budget",
            "reproducibility": reproducibility,
            "measurement_set_sha256": sha256_json(_measurement_signature(measurements)),
        },
        coverage=coverage,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_report(
        report,
        args.output_dir / "report.json",
        args.output_dir / "report.md",
        exit_code,
        args.output_dir / "report.html",
    )
    print(json.dumps({"exit_code": exit_code, "measurements": len(measurements), "errors": errors}, ensure_ascii=False))
    return exit_code


def command_evaluate(args: argparse.Namespace) -> int:
    data = read_json(args.report)
    if not isinstance(data, dict) or data.get("schema_version") != 3:
        raise ValueError("evaluate requires a schema-v3 run report")
    coverage = data.get("coverage")
    provenance = data.get("provenance")
    rows = data.get("measurements")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        raise ValueError("cannot evaluate an incomplete report")
    if any(coverage.get(key) != [] for key in ("missing_cases", "unexpected_cases", "duplicate_cases")):
        raise ValueError("cannot evaluate a report with missing, unexpected, or duplicate cases")
    expected_cases = coverage.get("expected_cases")
    observed_cases = coverage.get("observed_cases")
    if (
        not isinstance(expected_cases, list)
        or not isinstance(observed_cases, list)
        or len(expected_cases) != len(set(expected_cases))
        or observed_cases != expected_cases
    ):
        raise ValueError("report case coverage is malformed or out of manifest order")
    if not isinstance(provenance, dict) or not isinstance(rows, list):
        raise ValueError("report provenance or measurements are invalid")
    if len(rows) != len(expected_cases):
        raise ValueError("report measurement count does not match complete case coverage")
    row_keys = [
        f"{row.get('scenario_id')}:{row.get('size')}"
        for row in rows if isinstance(row, dict)
    ]
    if row_keys != expected_cases:
        raise ValueError("report measurement rows do not match declared case coverage")
    reproducibility = provenance.get("reproducibility")
    if (
        not isinstance(reproducibility, dict)
        or reproducibility.get("required") is not True
        or reproducibility.get("runs_per_scenario") != 2
        or reproducibility.get("passed") is not True
        or reproducibility.get("mismatches") != []
    ):
        raise ValueError("cannot evaluate a report without successful two-pass reproducibility")
    if provenance.get("measurement_set_sha256") != sha256_json(rows):
        raise ValueError("report measurement checksum does not match its contents")
    if data.get("errors"):
        raise ValueError("cannot evaluate a report containing run errors")

    measurements: list[Measurement] = []
    allowed = set(Measurement.__dataclass_fields__)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("report contains a malformed measurement")
        values = {key: value for key, value in row.items() if key in allowed}
        values["error_ids"] = tuple(values.get("error_ids", ()))
        if values.get("status") not in {"success", "budget_exceeded"}:
            raise ValueError("cannot evaluate a report containing invalid/indeterminate measurements")
        if values.get("measurement_method") != "integer_budget_search_v3":
            raise ValueError("offline evaluation requires schema-v3 explicit-case measurements")
        measurements.append(Measurement(**values))
    baseline = load_baseline(args.baseline) if args.baseline else None
    runtime = load_runtime(args.runtime)
    report_runtime = data.get("runtime")
    if not isinstance(report_runtime, dict):
        raise ValueError("report runtime is malformed")
    runtime_fields = (
        "protocol", "octez_digest", "chain_id", "hard_gas_limit_per_operation"
    )
    mismatched_report_runtime = [
        key for key in runtime_fields if report_runtime.get(key) != runtime.to_dict().get(key)
    ]
    if mismatched_report_runtime:
        raise ValueError(
            "report runtime does not match runtime lock: " + ", ".join(mismatched_report_runtime)
        )
    if baseline is not None:
        if baseline.schema_version != 3 or {
            entry.get("measurement_method") for entry in baseline.scenarios.values()
        } != {"integer_budget_search_v3"}:
            raise ValueError("offline evaluation requires a schema-v3 measurement baseline")
        runtime_fields = (
            "protocol", "octez_digest", "chain_id", "hard_gas_limit_per_operation"
        )
        mismatches = [key for key in runtime_fields if baseline.runtime.get(key) != runtime.to_dict().get(key)]
        if mismatches:
            raise ValueError("baseline runtime does not match runtime lock: " + ", ".join(mismatches))
    policy = _load_policy(args.policy)
    decisions = evaluate_measurements(measurements, baseline, policy, runtime)
    exit_code = exit_code_for(decisions, [])
    report = RunReport(
        schema_version=3,
        generated_at_utc=utc_now(),
        runtime=data.get("runtime", {}),
        manifest_sha256=str(data.get("manifest_sha256", "")),
        measurements=measurements,
        decisions=decisions,
        errors=[],
        provenance={
            **provenance,
            "policy_evaluation": True,
            "source_report_sha256": sha256_json(data),
            "measurement_set_sha256": sha256_json(_measurement_signature(measurements)),
        },
        coverage=coverage,
    )
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing to write reports into a non-empty directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_report(
        report,
        args.output_dir / "report.json",
        args.output_dir / "report.md",
        exit_code,
        args.output_dir / "report.html",
    )
    print(json.dumps({"exit_code": exit_code, "measurements": len(measurements), "errors": []}, ensure_ascii=False))
    return exit_code


def command_validate(args: argparse.Namespace) -> int:
    runtime = load_runtime(args.runtime)
    manifest, scenarios, manifest_hash = load_manifest(args.manifest.resolve(), runtime)
    print(json.dumps({"valid": True, "scenarios": [scenario.scenario_id for scenario in scenarios], "manifest_sha256": manifest_hash}, ensure_ascii=False))
    return 0


def command_baseline_create(args: argparse.Namespace) -> int:
    data = read_json(args.report)
    if not isinstance(data, dict):
        print("refusing to create a baseline from a report that is not a JSON object", file=sys.stderr)
        return 2
    if int(data.get("exit_code", 0)) != 0:
        print("refusing to create baseline from a non-zero report", file=sys.stderr)
        return 2
    report_schema = data.get("schema_version")
    if type(report_schema) is not int or report_schema not in {2, 3}:
        print("refusing to create baseline from an unsupported report schema", file=sys.stderr)
        return 2
    if report_schema == 3 and (not isinstance(data.get("coverage"), dict) or data["coverage"].get("complete") is not True):
        print("refusing to create baseline from an incomplete report", file=sys.stderr)
        return 2
    errors = data.get("errors", [])
    provenance = data.get("provenance", {})
    report_measurements = data.get("measurements", [])
    if not isinstance(errors, list) or not isinstance(provenance, dict):
        print("refusing to create a baseline from an invalid report structure", file=sys.stderr)
        return 2
    if not isinstance(report_measurements, list) or not report_measurements:
        print("refusing to create an empty baseline", file=sys.stderr)
        return 2
    reproducibility = provenance.get("reproducibility", {})
    if not isinstance(reproducibility, dict):
        print("refusing to create a baseline from invalid reproducibility metadata", file=sys.stderr)
        return 2
    if errors or reproducibility.get("required") and not reproducibility.get("passed"):
        print("refusing to create baseline from an errored or non-reproducible report", file=sys.stderr)
        return 2
    if report_schema == 3:
        coverage = data["coverage"]
        if any(coverage.get(key) != [] for key in ("missing_cases", "unexpected_cases", "duplicate_cases")):
            print("refusing to create baseline from inconsistent case coverage", file=sys.stderr)
            return 2
        expected_cases = coverage.get("expected_cases")
        observed_cases = coverage.get("observed_cases")
        if (
            not isinstance(expected_cases, list)
            or not isinstance(observed_cases, list)
            or expected_cases != observed_cases
            or len(expected_cases) != len(report_measurements)
        ):
            print("refusing to create baseline from malformed case coverage", file=sys.stderr)
            return 2
        if provenance.get("measurement_set_sha256") != sha256_json(report_measurements):
            print("refusing to create baseline with an invalid measurement checksum", file=sys.stderr)
            return 2
    runtime = load_runtime(args.runtime)
    from .models import Measurement

    measurements = []
    for item in report_measurements:
        if not isinstance(item, dict):
            print("refusing to create a baseline with a malformed measurement", file=sys.stderr)
            return 2
        allowed = {field for field in Measurement.__dataclass_fields__}
        values = {key: value for key, value in item.items() if key in allowed}
        values["error_ids"] = tuple(values.get("error_ids", ()))
        measurements.append(Measurement(**values))
    baseline = build_baseline(runtime, measurements, schema_version=report_schema)
    save_baseline(args.output, baseline)
    print(json.dumps({"created": str(args.output), "scenarios": len(baseline.scenarios)}, ensure_ascii=False))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(PROJECT_ROOT / "tests"), "-p", args.pattern, "-v"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode


def command_acceptance() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.acceptance.bounded_sequence", "-v"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            runtime = load_runtime(args.runtime)
            print(json.dumps(doctor(runtime), ensure_ascii=False, indent=2))
            return 0
        if args.command == "capture":
            results = [capture_contract(address, args.output_dir, args.rpc, args.block) for address in args.address]
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0
        if args.command == "capture-big-map":
            result = capture_big_map_key(args.ptr, args.key, args.output, args.api)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "variant":
            result = make_noop_gas_variant(args.source.resolve(), args.output.resolve(), args.repetitions)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "controlled-manifest":
            result = make_controlled_manifest(
                args.source_manifest.resolve(),
                args.output_manifest.resolve(),
                args.repetitions,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate":
            return command_validate(args)
        if args.command == "run":
            return command_run(args)
        if args.command == "evaluate":
            return command_evaluate(args)
        if args.command == "baseline" and args.baseline_command == "create":
            return command_baseline_create(args)
        if args.command == "verify":
            return command_verify(args)
        if args.command == "acceptance":
            return command_acceptance()
        raise RuntimeError(f"unsupported command: {args.command}")
    except (ManifestError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (CaptureError, RuntimeErrorState) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
