from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .growth import resolve_case
from .models import ExecutionContext, ExecutionResult, Measurement, RuntimeLock, ScenarioSpec
from .util import canonical_json, sha256_bytes, sha256_json


class Runner(Protocol):
    def run_code(
        self,
        script: Path,
        storage: Any,
        input_value: Any,
        context: ExecutionContext,
        gas_budget: int,
    ) -> ExecutionResult: ...


def _is_gas_failure(result: ExecutionResult) -> bool:
    return result.status == "gas_exhausted"


def _semantic_signature(result: ExecutionResult) -> str:
    return canonical_json(
        {
            "status": result.status,
            "storage": result.storage,
            "operations": result.operations,
            "lazy_storage_diff": result.lazy_storage_diff,
            "events": result.events,
        }
    )


def find_minimum_budget(
    runner: Runner,
    script: Path,
    storage: Any,
    input_value: Any,
    context: ExecutionContext,
    gas_cap: int,
) -> tuple[str, int | None, int | None, int | None, ExecutionResult]:
    if gas_cap <= 0:
        raise ValueError("gas_cap must be positive")
    at_cap = runner.run_code(script, storage, input_value, context, gas_cap)
    if not at_cap.succeeded:
        if _is_gas_failure(at_cap):
            return "budget_exceeded", None, gas_cap, None, at_cap
        return "invalid", None, None, None, at_cap

    low = 0
    high = gas_cap
    at_one = runner.run_code(script, storage, input_value, context, 1)
    if at_one.succeeded:
        if _semantic_signature(at_one) != _semantic_signature(at_cap):
            return "budget_dependent", None, None, None, at_cap
        return "success", 1, None, 1, at_one
    if not _is_gas_failure(at_one):
        return "invalid", None, None, None, at_one

    while high - low > 1:
        middle = (low + high) // 2
        result = runner.run_code(script, storage, input_value, context, middle)
        if result.succeeded:
            if _semantic_signature(result) != _semantic_signature(at_cap):
                return "budget_dependent", None, low, high, result
            high = middle
        elif _is_gas_failure(result):
            low = middle
        else:
            return "invalid", None, low, high, result

    final_failure = runner.run_code(script, storage, input_value, context, low)
    final_success = runner.run_code(script, storage, input_value, context, high)
    if not _is_gas_failure(final_failure) or not final_success.succeeded:
        return "invalid", None, low, high, final_success
    if _semantic_signature(final_success) != _semantic_signature(at_cap):
        return "budget_dependent", None, low, high, final_success
    return "success", high, low, high, final_success


def measure_scenario(
    scenario: ScenarioSpec,
    runtime: RuntimeLock,
    runner: Runner,
    manifest_dir: Path,
    on_case: Callable[[Measurement, int, int], None] | None = None,
) -> list[Measurement]:
    script_path = manifest_dir / scenario.script
    code_bytes = script_path.read_bytes()
    code_hash = sha256_bytes(code_bytes)
    gas_cap = (
        scenario.measurement_cap
        if scenario.manifest_schema_version == 2 and scenario.measurement_cap is not None
        else scenario.gas_cap
        if scenario.manifest_schema_version == 1 and scenario.gas_cap is not None
        else runtime.hard_gas_limit_per_operation
    )
    measurements: list[Measurement] = []

    for size in scenario.sizes:
        resolved = resolve_case(scenario, size, manifest_dir, runtime)
        storage, input_value, context = resolved.storage, resolved.input_value, resolved.context
        fixture_hash = sha256_json({"storage": storage, "input": input_value, "context": context.to_dict()})
        status, minimum, last_failed, first_success, last_result = find_minimum_budget(
            runner,
            script_path,
            storage,
            input_value,
            context,
            gas_cap,
        )
        storage_bytes = len(canonical_json(storage).encode("utf-8"))
        measurement = Measurement(
                scenario_id=scenario.scenario_id,
                size=size,
                status=status,
                minimum_successful_gas_budget=minimum,
                last_failed_gas_budget=last_failed,
                first_successful_gas_budget=first_success,
                gas_cap=gas_cap,
                storage_bytes=storage_bytes,
                operations_count=len(last_result.operations) if last_result.succeeded else None,
                fixture_sha256=fixture_hash,
                code_sha256=code_hash,
                measurement_method=(
                    "integer_budget_search_v3"
                    if scenario.manifest_schema_version == 2
                    else "integer_budget_search_v2"
                ),
                state_validity=resolved.state_validity,
                error_ids=last_result.error_ids,
                cap_kind=(
                    "protocol"
                    if gas_cap == runtime.hard_gas_limit_per_operation
                    else "search"
                    if scenario.manifest_schema_version == 2
                    else "policy"
                ),
                context_sha256=sha256_json(context.to_dict()),
                semantic_output_sha256=(
                    sha256_bytes(_semantic_signature(last_result).encode("utf-8"))
                    if last_result.succeeded
                    else None
                ),
                semantic_output=(
                    {
                        "storage": last_result.storage,
                        "operations": last_result.operations,
                        "lazy_storage_diff": last_result.lazy_storage_diff,
                        "events": last_result.events,
                    }
                    if last_result.succeeded and scenario.manifest_schema_version == 2
                    else None
                ),
                input_bytes=len(canonical_json(input_value).encode("utf-8")),
                state_trace_sha256=resolved.sequence_trace_sha256,
            )
        measurements.append(measurement)
        if on_case is not None:
            on_case(measurement, len(measurements), len(scenario.sizes))
    return measurements
