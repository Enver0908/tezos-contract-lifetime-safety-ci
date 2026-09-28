from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tlsci.growth import resolve_case
from tlsci.models import ExecutionContext, ExecutionResult, RuntimeLock
from tlsci.octez import OctezRunner
from tlsci.runtime import load_runtime
from tlsci.util import canonical_json, read_json, sha256_bytes, sha256_json, write_json
from tlsci.validation import load_manifest


PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT / "fixtures" / "bounded-sequence-v1" / "protocol.json"
CHECKPOINT_CALLS = (0, 1, 8, 63, 64, 65, 256, 1024, 4096)
MAXIMUM_CALLS = 4096
STORAGE_LIMIT = 64
MAX_SEQUENCE_SECONDS = 6 * 60 * 60
MEASUREMENT_METHOD = "integer_budget_search_v3"


class SequenceError(ValueError):
    """The bounded-sequence evidence does not satisfy its locked protocol."""


class SequenceRunner(Protocol):
    def run_code(
        self,
        script: Path,
        storage: Any,
        input_value: Any,
        context: ExecutionContext,
        gas_budget: int,
    ) -> ExecutionResult: ...


def _expected_storage(completed_calls: int, storage_limit: int = STORAGE_LIMIT) -> list[dict[str, str]]:
    return [{"int": "1"} for _ in range(min(completed_calls, storage_limit))]


def _expected_next_call_output(completed_calls: int, storage_limit: int = STORAGE_LIMIT) -> dict[str, Any]:
    """Return the deterministic semantic output of one probe from a reached checkpoint."""
    return {
        "storage": _expected_storage(completed_calls + 1, storage_limit),
        "operations": [],
        "lazy_storage_diff": None,
        "events": [],
    }


def _post_saturation_budgets(measurements_by_calls: dict[int, dict[str, Any]]) -> dict[str, int]:
    """Use JSON object string keys consistently in in-memory and persisted reports."""
    return {
        str(calls): int(measurements_by_calls[calls]["minimum_successful_gas_budget"])
        for calls in (64, 65, 256, 1024, 4096)
    }


def _contained_source(protocol_path: Path, reference: str) -> Path:
    project = protocol_path.resolve().parents[2]
    resolved = (protocol_path.parent / reference).resolve()
    if not resolved.is_relative_to(project) or not resolved.is_file():
        raise SequenceError(f"protocol source path is missing or outside the repository: {reference}")
    return resolved


def load_protocol(
    protocol_path: Path = PROTOCOL_PATH,
    runtime_path: Path | None = None,
) -> tuple[dict[str, Any], RuntimeLock, dict[str, Path]]:
    protocol_path = protocol_path.resolve()
    protocol = read_json(protocol_path)
    if not isinstance(protocol, dict) or protocol.get("schema_version") != 1:
        raise SequenceError("unsupported bounded-sequence protocol schema")
    if protocol.get("scenario_id") != "bounded_last64_lifetime_v1":
        raise SequenceError("bounded-sequence scenario id differs from the locked value")
    if protocol.get("maximum_completed_calls") != MAXIMUM_CALLS:
        raise SequenceError("maximum_completed_calls must remain 4096")
    if protocol.get("storage_limit") != STORAGE_LIMIT:
        raise SequenceError("storage_limit must remain 64")
    if tuple(protocol.get("checkpoint_calls", ())) != CHECKPOINT_CALLS:
        raise SequenceError("checkpoint_calls differ from the predeclared checkpoints")
    if protocol.get("repetitions_per_manifest") != 2:
        raise SequenceError("each checkpoint manifest must require exactly two measurement passes")
    if protocol.get("call_timeout_seconds") != 60:
        raise SequenceError("per-call timeout must remain 60 seconds")
    if protocol.get("maximum_sequence_seconds") != MAX_SEQUENCE_SECONDS:
        raise SequenceError("maximum sequence runtime must remain six hours")
    expectation = protocol.get("predeclared_expectation")
    if not isinstance(expectation, dict) or expectation != {
        "initial_storage": [],
        "parameter": {"prim": "Unit"},
        "prepended_value": {"int": "1"},
        "storage_cardinality_after_calls": "min(completed_calls, 64)",
        "storage_after_64_calls": [{"int": "1"}],
        "post_saturation_budget_rule": (
            "minimum successful gas budget is identical at completed_calls 64, 65, 256, 1024, and 4096"
        ),
        "growth_v2_thresholds": "unchanged; the new bounded-state condition is exact equality after saturation",
    }:
        raise SequenceError("predeclared semantics or acceptance rule has changed")

    runtime_path = (runtime_path or PROJECT / "runtime.lock.json").resolve()
    runtime = load_runtime(runtime_path)
    runtime_fields = (
        "octez_digest", "octez_version", "protocol", "chain_id", "hard_gas_limit_per_operation"
    )
    declared_runtime = protocol.get("runtime")
    if not isinstance(declared_runtime, dict):
        raise SequenceError("bounded-sequence protocol is missing its pinned runtime")
    for field in runtime_fields:
        if declared_runtime.get(field) != getattr(runtime, field):
            raise SequenceError(f"bounded-sequence runtime lock mismatch: {field}")

    raw_sources = protocol.get("source_files")
    if not isinstance(raw_sources, dict) or set(raw_sources) != {"script", "input", "context"}:
        raise SequenceError("source_files must identify exactly the script, input, and context")
    sources: dict[str, Path] = {}
    for name, source in raw_sources.items():
        if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
            raise SequenceError(f"protocol source metadata is malformed: {name}")
        path = _contained_source(protocol_path, str(source["path"]))
        actual = sha256_bytes(path.read_bytes())
        if actual != source["sha256"]:
            raise SequenceError(f"pinned {name} source checksum changed: {path}")
        sources[name] = path
    if read_json(sources["input"]) != {"prim": "Unit"}:
        raise SequenceError("pinned sequence input is not the declared Unit value")
    if read_json(sources["context"]).get("chain_id") != runtime.chain_id:
        raise SequenceError("pinned sequence context uses another chain id")
    return protocol, runtime, sources


def _transition_call(
    runner: SequenceRunner,
    script: Path,
    storage: Any,
    input_value: Any,
    context: ExecutionContext,
    runtime: RuntimeLock,
) -> ExecutionResult:
    return runner.run_code(
        script,
        storage,
        input_value,
        context,
        runtime.hard_gas_limit_per_operation,
    )


def collect_transition_chain(
    runner: SequenceRunner,
    script: Path,
    runtime: RuntimeLock,
    context: ExecutionContext,
    input_value: Any,
    *,
    initial_storage: Any | None = None,
    maximum_calls: int = MAXIMUM_CALLS,
    storage_limit: int = STORAGE_LIMIT,
    checkpoints: tuple[int, ...] = CHECKPOINT_CALLS,
    maximum_seconds: int = MAX_SEQUENCE_SECONDS,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, Any], dict[int, Any]]:
    if maximum_calls <= 0 or storage_limit <= 0 or maximum_seconds <= 0:
        raise ValueError("sequence limits must be positive")
    if tuple(sorted(set(checkpoints))) != checkpoints or checkpoints[0] != 0:
        raise ValueError("checkpoints must be strictly increasing and begin at zero")
    if checkpoints[-1] != maximum_calls:
        raise ValueError("the final checkpoint must equal maximum_calls")
    storage = [] if initial_storage is None else initial_storage
    if storage != []:
        raise SequenceError("the lifetime sequence must start with an empty storage list")
    input_hash = sha256_json(input_value)
    context_hash = sha256_json(context.to_dict())
    snapshots: dict[int, Any] = {0: []}
    steps: list[dict[str, Any]] = []
    started = time.monotonic()

    for call_number in range(1, maximum_calls + 1):
        if time.monotonic() - started > maximum_seconds:
            raise TimeoutError(f"bounded sequence exceeded its {maximum_seconds}-second limit")
        before_hash = sha256_json(storage)
        before_count = len(storage) if isinstance(storage, list) else -1
        result = _transition_call(runner, script, storage, input_value, context, runtime)
        if not result.succeeded:
            raise SequenceError(
                f"transition {call_number} did not succeed: status={result.status}; "
                f"error_ids={list(result.error_ids)}; {result.raw_stderr_tail}"
            )
        if not isinstance(result.storage, list):
            raise SequenceError(f"transition {call_number} returned non-list storage")
        if len(result.storage) > storage_limit:
            raise SequenceError(f"transition {call_number} exceeded the {storage_limit}-item storage limit")
        expected = _expected_storage(call_number, storage_limit)
        if canonical_json(result.storage) != canonical_json(expected):
            raise SequenceError(f"transition {call_number} returned unexpected ordered storage")
        if result.operations or result.events or result.lazy_storage_diff not in (None, []):
            raise SequenceError(f"transition {call_number} produced an effect outside this single-script sequence")

        output_hash = sha256_json(result.storage)
        steps.append(
            {
                "call_number": call_number,
                "completed_calls_before": call_number - 1,
                "completed_calls_after": call_number,
                "storage_cardinality_before": before_count,
                "storage_cardinality_after": len(result.storage),
                "input_storage_sha256": before_hash,
                "input_sha256": input_hash,
                "context_sha256": context_hash,
                "status": result.status,
                "minimum_sequence_call_gas_budget": runtime.hard_gas_limit_per_operation,
                "storage_after": result.storage,
                "storage_after_sha256": output_hash,
                "operations_count": len(result.operations),
                "events_count": len(result.events),
                "lazy_storage_diff_sha256": sha256_json(result.lazy_storage_diff),
            }
        )
        storage = result.storage
        if call_number in checkpoints:
            snapshots[call_number] = storage
        if progress is not None and (call_number % 64 == 0 or call_number == maximum_calls):
            progress(call_number, maximum_calls)

    trace = {
        "schema_version": 1,
        "scenario_id": "bounded_last64_lifetime_v1",
        "state_transition_method": "each successful pinned-Octez run_code storage output is the next call input",
        "octez_digest": runtime.octez_digest,
        "octez_version": runtime.octez_version,
        "protocol": runtime.protocol,
        "chain_id": runtime.chain_id,
        "script_sha256": sha256_bytes(script.read_bytes()),
        "input_sha256": input_hash,
        "context_sha256": context_hash,
        "initial_storage": [],
        "maximum_completed_calls": maximum_calls,
        "storage_limit": storage_limit,
        "checkpoint_calls": list(checkpoints),
        "internal_operations_executed": False,
        "steps": steps,
        "snapshots": [
            {
                "size": call_number,
                "completed_calls": call_number,
                "storage_cardinality": len(snapshots[call_number]),
                "storage_sha256": sha256_json(snapshots[call_number]),
                "input_sha256": input_hash,
                "context_sha256": context_hash,
                "storage": snapshots[call_number],
            }
            for call_number in checkpoints
        ],
    }
    return trace, snapshots


def build_sequence_corpus(
    output_dir: Path,
    *,
    protocol_path: Path = PROTOCOL_PATH,
    runtime_path: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    protocol, runtime, sources = load_protocol(protocol_path, runtime_path)
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=False, exist_ok=False)
    contracts_dir = output_dir / "contracts" / "synthetic"
    contracts_dir.mkdir(parents=True)
    shutil.copyfile(sources["script"], contracts_dir / "bounded_last64_append.json")
    cases_dir = output_dir / "cases" / protocol["scenario_id"]
    cases_dir.mkdir(parents=True)
    shutil.copyfile(sources["input"], cases_dir / "input.json")
    shutil.copyfile(sources["context"], cases_dir / "context.json")

    context_data = read_json(cases_dir / "context.json")
    context = ExecutionContext.from_dict(context_data, runtime, output_dir)
    script = contracts_dir / "bounded_last64_append.json"
    input_value = read_json(cases_dir / "input.json")
    with OctezRunner(runtime, PROJECT, timeout=protocol["call_timeout_seconds"]) as runner:
        trace, snapshots = collect_transition_chain(
            runner,
            script,
            runtime,
            context,
            input_value,
            maximum_calls=protocol["maximum_completed_calls"],
            storage_limit=protocol["storage_limit"],
            checkpoints=tuple(protocol["checkpoint_calls"]),
            maximum_seconds=protocol["maximum_sequence_seconds"],
            progress=progress,
        )

    trace_path = output_dir / "state-trace.json"
    write_json(trace_path, trace)
    trace_hash = sha256_bytes(trace_path.read_bytes())
    cases: list[dict[str, Any]] = []
    for sequence_index, call_number in enumerate(protocol["checkpoint_calls"]):
        storage = snapshots[call_number]
        suffix = str(call_number)
        storage_path = f"cases/{protocol['scenario_id']}/{suffix}.storage.json"
        input_path = f"cases/{protocol['scenario_id']}/{suffix}.input.json"
        context_path = f"cases/{protocol['scenario_id']}/{suffix}.context.json"
        write_json(output_dir / storage_path, storage)
        shutil.copyfile(cases_dir / "input.json", output_dir / input_path)
        shutil.copyfile(cases_dir / "context.json", output_dir / context_path)
        cases.append(
            {
                "size": call_number,
                "storage_file": storage_path,
                "input_file": input_path,
                "context_file": context_path,
                "state_validity": "single_script_sequence_validated",
                "sequence_trace_file": "state-trace.json",
                "sequence_index": sequence_index,
            }
        )

    manifest = {
        "schema_version": 2,
        "name": "bounded-last64-real-transition-sequence-v1",
        "description": (
            "Fresh empty state advanced through 4096 sequential calls under the pinned mockup; "
            "case size means completed calls and is not stored-list cardinality."
        ),
        "repetitions": 2,
        "runtime": {
            key: getattr(runtime, key)
            for key in ("octez_digest", "octez_version", "protocol", "chain_id", "hard_gas_limit_per_operation")
        },
        "scenarios": [
            {
                "id": protocol["scenario_id"],
                "script": "contracts/synthetic/bounded_last64_append.json",
                "storage_generator": "explicit",
                "input_generator": "explicit",
                "expected": "success",
                "measurement_cap": runtime.hard_gas_limit_per_operation,
                "cases": cases,
            }
        ],
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    _, _, manifest_hash = load_manifest(manifest_path, runtime)
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_hash,
        "state_trace": str(trace_path),
        "state_trace_sha256": trace_hash,
        "transition_count": len(trace["steps"]),
        "checkpoint_count": len(cases),
        "final_storage_cardinality": len(snapshots[MAXIMUM_CALLS]),
        "runtime": runtime.to_dict(),
    }


def _validate_trace(
    trace: Any,
    protocol: dict[str, Any],
    runtime: RuntimeLock,
    script: Path,
    input_value: Any,
    context: ExecutionContext,
    cases: list[Any],
) -> tuple[list[str], dict[int, dict[str, Any]]]:
    errors: list[str] = []
    if not isinstance(trace, dict) or trace.get("schema_version") != 1:
        raise SequenceError("state trace has an unsupported schema")
    expected_top = {
        "scenario_id": protocol["scenario_id"],
        "octez_digest": runtime.octez_digest,
        "octez_version": runtime.octez_version,
        "protocol": runtime.protocol,
        "chain_id": runtime.chain_id,
        "script_sha256": sha256_bytes(script.read_bytes()),
        "input_sha256": sha256_json(input_value),
        "context_sha256": sha256_json(context.to_dict()),
        "initial_storage": [],
        "maximum_completed_calls": protocol["maximum_completed_calls"],
        "storage_limit": protocol["storage_limit"],
        "checkpoint_calls": protocol["checkpoint_calls"],
        "internal_operations_executed": False,
    }
    for key, value in expected_top.items():
        if trace.get(key) != value:
            raise SequenceError(f"state trace metadata mismatch: {key}")
    steps = trace.get("steps")
    if not isinstance(steps, list) or len(steps) != protocol["maximum_completed_calls"]:
        raise SequenceError("state trace must contain all 4096 sequential transition records")
    previous: Any = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise SequenceError(f"state trace transition {index} is malformed")
        expected = {
            "call_number": index,
            "completed_calls_before": index - 1,
            "completed_calls_after": index,
            "storage_cardinality_before": len(previous),
            "storage_cardinality_after": min(index, protocol["storage_limit"]),
            "input_storage_sha256": sha256_json(previous),
            "input_sha256": expected_top["input_sha256"],
            "context_sha256": expected_top["context_sha256"],
            "status": "success",
            "minimum_sequence_call_gas_budget": runtime.hard_gas_limit_per_operation,
            "storage_after": _expected_storage(index, protocol["storage_limit"]),
            "operations_count": 0,
            "events_count": 0,
            "lazy_storage_diff_sha256": sha256_json(None),
        }
        for key, value in expected.items():
            if step.get(key) != value:
                raise SequenceError(f"state trace transition {index} mismatch: {key}")
        output_hash = sha256_json(expected["storage_after"])
        if step.get("storage_after_sha256") != output_hash:
            raise SequenceError(f"state trace transition {index} output checksum mismatch")
        previous = expected["storage_after"]

    raw_snapshots = trace.get("snapshots")
    checkpoints = protocol["checkpoint_calls"]
    if not isinstance(raw_snapshots, list) or len(raw_snapshots) != len(checkpoints):
        raise SequenceError("state trace checkpoint snapshots are incomplete")
    snapshots: dict[int, dict[str, Any]] = {}
    for index, (call_number, snapshot) in enumerate(zip(checkpoints, raw_snapshots)):
        storage = _expected_storage(call_number, protocol["storage_limit"])
        expected = {
            "size": call_number,
            "completed_calls": call_number,
            "storage_cardinality": len(storage),
            "storage_sha256": sha256_json(storage),
            "input_sha256": expected_top["input_sha256"],
            "context_sha256": expected_top["context_sha256"],
            "storage": storage,
        }
        if snapshot != expected:
            raise SequenceError(f"state trace checkpoint {call_number} does not match its declared state")
        case = cases[index]
        if case.size != call_number or case.sequence_index != index:
            raise SequenceError(f"manifest case is mapped to the wrong completed-call checkpoint: {call_number}")
        snapshots[call_number] = snapshot
    return errors, snapshots


def _semantic_output(result: ExecutionResult) -> dict[str, Any]:
    return {
        "storage": result.storage,
        "operations": result.operations,
        "lazy_storage_diff": result.lazy_storage_diff,
        "events": result.events,
    }


def verify_bounded_artifacts(
    corpus_dir: Path,
    report_path: Path,
    *,
    protocol_path: Path = PROTOCOL_PATH,
    runtime_path: Path | None = None,
    replay_direct_boundaries: bool = True,
) -> dict[str, Any]:
    protocol, runtime, _ = load_protocol(protocol_path, runtime_path)
    corpus_dir = corpus_dir.resolve()
    report_path = report_path.resolve()
    manifest_path = corpus_dir / "manifest.json"
    manifest_data, scenarios, manifest_hash = load_manifest(manifest_path, runtime)
    if len(scenarios) != 1 or scenarios[0].scenario_id != protocol["scenario_id"]:
        raise SequenceError("checkpoint manifest does not contain exactly the pinned bounded sequence")
    scenario = scenarios[0]
    if tuple(case.size for case in scenario.cases) != tuple(protocol["checkpoint_calls"]):
        raise SequenceError("checkpoint manifest is missing, reordering, or adding a measurement point")
    expected_script = corpus_dir / "contracts" / "synthetic" / "bounded_last64_append.json"
    if sha256_bytes(expected_script.read_bytes()) != read_json(protocol_path).get("source_files", {}).get("script", {}).get("sha256"):
        raise SequenceError("checkpoint manifest uses a script other than the pinned bounded contract")
    initial_case = resolve_case(scenario, 0, corpus_dir, runtime)
    trace_path = corpus_dir / scenario.cases[0].sequence_trace_file
    trace = read_json(trace_path)
    trace_hash = sha256_bytes(trace_path.read_bytes())
    _, trace_snapshots = _validate_trace(
        trace,
        protocol,
        runtime,
        expected_script,
        initial_case.input_value,
        initial_case.context,
        list(scenario.cases),
    )

    report = read_json(report_path)
    if not isinstance(report, dict) or report.get("schema_version") != 3:
        raise SequenceError("bounded checkpoint report must use schema 3")
    if report.get("manifest_sha256") != manifest_hash:
        raise SequenceError("checkpoint report manifest checksum mismatch")
    for field in ("octez_digest", "octez_version", "protocol", "chain_id", "hard_gas_limit_per_operation"):
        if report.get("runtime", {}).get(field) != getattr(runtime, field):
            raise SequenceError(f"checkpoint report runtime mismatch: {field}")
    coverage = report.get("coverage")
    expected_keys = [f"{protocol['scenario_id']}:{size}" for size in protocol["checkpoint_calls"]]
    if (
        not isinstance(coverage, dict)
        or coverage.get("complete") is not True
        or coverage.get("expected_cases") != expected_keys
        or coverage.get("observed_cases") != expected_keys
        or any(coverage.get(key) != [] for key in ("missing_cases", "unexpected_cases", "duplicate_cases"))
    ):
        raise SequenceError("checkpoint report does not cover all nine cases exactly once in protocol order")
    provenance = report.get("provenance")
    repro = provenance.get("reproducibility") if isinstance(provenance, dict) else None
    measurements = report.get("measurements")
    decisions = report.get("decisions")
    if (
        not isinstance(repro, dict)
        or repro.get("required") is not True
        or repro.get("runs_per_scenario") != 2
        or repro.get("passed") is not True
        or repro.get("mismatches") != []
        or not isinstance(measurements, list)
        or len(measurements) != len(expected_keys)
        or not isinstance(decisions, list)
        or len(decisions) != len(expected_keys)
        or report.get("errors") != []
        or report.get("exit_code") != 0
    ):
        raise SequenceError("checkpoint report has errors, incomplete repetitions, or failed policy")
    if provenance.get("manifest_data") != manifest_data:
        raise SequenceError("checkpoint report embeds different manifest data")
    if (
        provenance.get("measurement_limit") != "single_script; internal operations are not executed"
        or provenance.get("gas_metric") != "minimum_successful_integer_gas_budget"
    ):
        raise SequenceError("checkpoint report misstates its execution scope or gas metric")
    if provenance.get("measurement_set_sha256") != sha256_json(measurements):
        raise SequenceError("checkpoint measurement-set checksum mismatch")

    measurements_by_calls: dict[int, dict[str, Any]] = {}
    decisions_by_calls: dict[int, dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("scenario_id") != protocol["scenario_id"]:
            raise SequenceError("checkpoint report contains a malformed or unexpected policy decision")
        call_count = decision.get("size")
        if type(call_count) is not int or call_count in decisions_by_calls:
            raise SequenceError("checkpoint report has an invalid or duplicate policy-decision checkpoint")
        if decision.get("decision") != "pass" or decision.get("rule_ids") != []:
            raise SequenceError(f"policy did not pass at completed_calls={call_count}")
        decisions_by_calls[call_count] = decision

    manifest_dir = manifest_path.parent
    for case in scenario.cases:
        row = next(
            (item for item in measurements if isinstance(item, dict) and item.get("size") == case.size),
            None,
        )
        if row is None:
            raise SequenceError(f"checkpoint measurement is missing at completed_calls={case.size}")
        resolved = resolve_case(scenario, case.size, manifest_dir, runtime)
        expected_fixture_hash = sha256_json(
            {
                "storage": resolved.storage,
                "input": resolved.input_value,
                "context": resolved.context.to_dict(),
            }
        )
        if row.get("scenario_id") != protocol["scenario_id"]:
            raise SequenceError(f"unexpected scenario id at checkpoint {case.size}")
        for key, value in {
            "status": "success",
            "measurement_method": MEASUREMENT_METHOD,
            "execution_scope": "single_script",
            "state_validity": "single_script_sequence_validated",
            "internal_operations_executed": False,
            "minimum_successful_gas_budget": row.get("minimum_successful_gas_budget"),
            "fixture_sha256": expected_fixture_hash,
            "code_sha256": sha256_bytes(expected_script.read_bytes()),
            "context_sha256": sha256_json(resolved.context.to_dict()),
            "state_trace_sha256": trace_hash,
            "gas_cap": runtime.hard_gas_limit_per_operation,
            "cap_kind": "protocol",
            "first_successful_gas_budget": row.get("minimum_successful_gas_budget"),
            "operations_count": 0,
        }.items():
            if row.get(key) != value:
                raise SequenceError(f"checkpoint report field {key} is inconsistent at completed_calls={case.size}")
        minimum = row.get("minimum_successful_gas_budget")
        lower = row.get("last_failed_gas_budget")
        if type(minimum) is not int or not 1 <= minimum <= runtime.hard_gas_limit_per_operation:
            raise SequenceError(f"minimum successful budget is invalid at checkpoint {case.size}")
        if lower != (minimum - 1 if minimum > 1 else None):
            raise SequenceError(f"gas search lower boundary is invalid at checkpoint {case.size}")
        semantic = row.get("semantic_output")
        # `size` names the input state after this many completed calls. The measured
        # Octez execution is the next call from that state, so its result is S_(k+1),
        # not the checkpoint input S_k. At k=4096 this is a separate, non-advancing
        # measurement probe: it is not added to the 4,096-transition history trace.
        expected_output = _expected_next_call_output(case.size, protocol["storage_limit"])
        if case.size < protocol["maximum_completed_calls"]:
            next_trace_step = trace["steps"][case.size]
            if next_trace_step.get("storage_after") != expected_output["storage"]:
                raise SequenceError(
                    f"next trace transition differs from measured checkpoint output at completed_calls={case.size}"
                )
        if semantic != expected_output:
            raise SequenceError(f"measured semantic output differs from state trace at checkpoint {case.size}")
        if row.get("semantic_output_sha256") != sha256_json({"status": "success", **expected_output}):
            raise SequenceError(f"semantic output checksum mismatch at checkpoint {case.size}")
        measurements_by_calls[case.size] = row
    if set(decisions_by_calls) != set(protocol["checkpoint_calls"]):
        raise SequenceError("checkpoint policy decisions do not match the nine declared cases")

    plateau_calls = (64, 65, 256, 1024, 4096)
    plateau_budgets = _post_saturation_budgets(measurements_by_calls)
    plateau_passed = len(set(plateau_budgets.values())) == 1
    direct_replays: list[dict[str, Any]] = []
    if replay_direct_boundaries:
        with OctezRunner(runtime, PROJECT, timeout=protocol["call_timeout_seconds"]) as runner:
            for case in scenario.cases:
                resolved = resolve_case(scenario, case.size, manifest_dir, runtime)
                measurement = measurements_by_calls[case.size]
                minimum = int(measurement["minimum_successful_gas_budget"])
                at_minimum = runner.run_code(
                    expected_script,
                    resolved.storage,
                    resolved.input_value,
                    resolved.context,
                    minimum,
                )
                if not at_minimum.succeeded or canonical_json(_semantic_output(at_minimum)) != canonical_json(
                    measurement["semantic_output"]
                ):
                    raise SequenceError(f"direct minimum-budget replay differs at completed_calls={case.size}")
                lower_status: str | None = None
                if minimum > 1:
                    below = runner.run_code(
                        expected_script,
                        resolved.storage,
                        resolved.input_value,
                        resolved.context,
                        minimum - 1,
                    )
                    lower_status = below.status
                    if lower_status != "gas_exhausted":
                        raise SequenceError(
                            f"minimum minus one did not exhaust gas at completed_calls={case.size}: {lower_status}"
                        )
                direct_replays.append(
                    {
                        "completed_calls": case.size,
                        "minimum_budget": minimum,
                        "minimum_succeeded": True,
                        "lower_budget": minimum - 1 if minimum > 1 else None,
                        "lower_status": lower_status,
                    }
                )

    issues = [] if plateau_passed else [
        "minimum successful gas budgets differ after the 64-item storage limit is reached"
    ]
    return {
        "schema_version": 1,
        "experiment_id": "bounded-sequence-v1",
        "result": "pass" if plateau_passed else "measured_technical_criteria_not_met",
        "technical_mvp_bounded_go": "go" if plateau_passed else "no-go",
        "issues": issues,
        "runtime": runtime.to_dict(),
        "manifest_sha256": manifest_hash,
        "report_sha256": sha256_bytes(report_path.read_bytes()),
        "state_trace_sha256": trace_hash,
        "transition_count": len(trace["steps"]),
        "checkpoint_count": len(scenario.cases),
        "coverage_complete": True,
        "two_pass_reproducibility": True,
        "direct_replays": direct_replays,
        "checkpoints": [
            {
                "completed_calls": calls,
                "storage_cardinality": trace_snapshots[calls]["storage_cardinality"],
                "minimum_successful_gas_budget": measurements_by_calls[calls]["minimum_successful_gas_budget"],
            }
            for calls in protocol["checkpoint_calls"]
        ],
        "post_saturation_calls": list(plateau_calls),
        "post_saturation_budgets": plateau_budgets,
        "post_saturation_exact_equality": plateau_passed,
        "claim_boundary": (
            "one synthetic Michelson fixture, one fixed input and context, pinned local Octez mockup; "
            "not a mainnet incident, user demand, payment intent, grant award, or revenue claim"
        ),
    }


def save_new_json(path: Path, value: Any) -> None:
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
