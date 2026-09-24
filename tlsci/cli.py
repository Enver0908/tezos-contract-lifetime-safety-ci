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
from .util import read_json, utc_now, write_json
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
    run.add_argument("--timeout", type=int, default=30)
    run.add_argument("--skip-doctor", action="store_true")

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


def command_run(args: argparse.Namespace) -> int:
    runtime = load_runtime(args.runtime)
    manifest_path = args.manifest.resolve()
    manifest, scenarios, manifest_hash = load_manifest(manifest_path, runtime)
    policy = _load_policy(args.policy)
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
            for scenario in scenarios:
                try:
                    first_run = measure_scenario(scenario, runtime, runner, manifest_path.parent)
                    measurements.extend(first_run)
                    if policy.require_reproducible:
                        second_run = measure_scenario(scenario, runtime, runner, manifest_path.parent)
                        if _measurement_signature(first_run) != _measurement_signature(second_run):
                            reproducibility["passed"] = False
                            mismatches = reproducibility["mismatches"]
                            assert isinstance(mismatches, list)
                            mismatches.append(scenario.scenario_id)
                            errors.append(
                                f"invalid: reproducibility mismatch for scenario {scenario.scenario_id}"
                            )
                except Exception as exc:
                    errors.append(f"{scenario.scenario_id}: {type(exc).__name__}: {exc}")
    except Exception as exc:
        errors.append(f"runner: {type(exc).__name__}: {exc}")
    baseline = load_baseline(args.baseline) if args.baseline else None
    if baseline is not None:
        if baseline.runtime.get("protocol") != runtime.protocol:
            errors.append("baseline protocol does not match runtime lock")
        if baseline.runtime.get("octez_digest") != runtime.octez_digest:
            errors.append("baseline Octez digest does not match runtime lock")
    decisions = evaluate_measurements(measurements, baseline, policy, runtime)
    exit_code = exit_code_for(decisions, errors)
    report = RunReport(
        schema_version=1,
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
            "reproducibility": reproducibility,
        },
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


def command_validate(args: argparse.Namespace) -> int:
    runtime = load_runtime(args.runtime)
    manifest, scenarios, manifest_hash = load_manifest(args.manifest.resolve(), runtime)
    print(json.dumps({"valid": True, "scenarios": [scenario.scenario_id for scenario in scenarios], "manifest_sha256": manifest_hash}, ensure_ascii=False))
    return 0


def command_baseline_create(args: argparse.Namespace) -> int:
    data = read_json(args.report)
    if int(data.get("exit_code", 0)) != 0:
        print("refusing to create baseline from a non-zero report", file=sys.stderr)
        return 2
    runtime = load_runtime(args.runtime)
    from .models import Measurement

    measurements = []
    for item in data.get("measurements", []):
        allowed = {field for field in Measurement.__dataclass_fields__}
        values = {key: value for key, value in item.items() if key in allowed}
        values["error_ids"] = tuple(values.get("error_ids", ()))
        measurements.append(Measurement(**values))
    baseline = build_baseline(runtime, measurements)
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
