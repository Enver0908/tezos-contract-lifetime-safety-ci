from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from .growth import generate_case
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
            return "over_hard_limit", None, gas_cap, None, at_cap
        return "invalid", None, None, None, at_cap

    low = 0
    high = gas_cap
    if runner.run_code(script, storage, input_value, context, 1).succeeded:
        return "success", 1, 0, 1, at_cap

    last_failure = runner.run_code(script, storage, input_value, context, 0)
    if not _is_gas_failure(last_failure):
        return "invalid", None, None, None, last_failure

    while high - low > 1:
        middle = (low + high) // 2
        result = runner.run_code(script, storage, input_value, context, middle)
        if result.succeeded:
            high = middle
        elif _is_gas_failure(result):
            low = middle
        else:
            return "invalid", None, low, high, result

    final_failure = runner.run_code(script, storage, input_value, context, low)
    final_success = runner.run_code(script, storage, input_value, context, high)
    if not _is_gas_failure(final_failure) or not final_success.succeeded:
        return "invalid", None, low, high, final_success
    return "success", high, low, high, final_success


def measure_scenario(
    scenario: ScenarioSpec,
    runtime: RuntimeLock,
    runner: Runner,
    manifest_dir: Path,
) -> list[Measurement]:
    script_path = manifest_dir / scenario.script
    code_bytes = script_path.read_bytes()
    code_hash = sha256_bytes(code_bytes)
    context = ExecutionContext.from_dict(scenario.context, runtime, manifest_dir)
    if scenario.entrypoint is not None:
        context = replace(context, entrypoint=scenario.entrypoint)
    gas_cap = (
        scenario.gas_cap
        if scenario.gas_cap is not None
        else runtime.hard_gas_limit_per_operation
    )
    measurements: list[Measurement] = []

    for size in scenario.sizes:
        storage, input_value = generate_case(scenario, size, manifest_dir)
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
        measurements.append(
            Measurement(
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
                error_ids=last_result.error_ids,
            )
        )
    return measurements
