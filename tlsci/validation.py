from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ExecutionContext, RuntimeLock, ScenarioSpec
from .util import read_json, sha256_bytes, sha256_json


class ManifestError(ValueError):
    pass


UNSUPPORTED_GAS_SENSITIVE_PRIMS = {"STEPS_TO_QUOTA"}


def _contains_unsupported_prim(value: Any) -> str | None:
    if isinstance(value, dict):
        prim = value.get("prim")
        if prim in UNSUPPORTED_GAS_SENSITIVE_PRIMS:
            return str(prim)
        for child in value.values():
            found = _contains_unsupported_prim(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_unsupported_prim(child)
            if found is not None:
                return found
    return None


def _contained_file(root: Path, reference: str, label: str) -> Path:
    path = Path(reference)
    if path.is_absolute():
        raise ManifestError(f"{label} must be relative to the manifest directory")
    resolved_root = root.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ManifestError(f"{label} escapes the manifest directory")
    if not resolved.is_file():
        raise ManifestError(f"{label} does not exist: {reference}")
    return resolved


def _validate_schema_v2_context_references(
    root: Path,
    scenario: ScenarioSpec,
    case: Any,
    data: Any,
) -> None:
    if not isinstance(data, dict):
        raise ManifestError(f"execution context for {scenario.scenario_id}:{case.size} must be a JSON object")
    for collection_name in ("other_contracts", "extra_big_maps"):
        # These fields are optional in the execution-context contract; when
        # present they must be JSON arrays. The model defaults omitted fields
        # to empty tuples, so validate the same effective empty value here.
        collection = data.get(collection_name, [])
        if not isinstance(collection, list):
            raise ManifestError(
                f"{collection_name} for {scenario.scenario_id}:{case.size} must be a JSON array"
            )
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                raise ManifestError(
                    f"{collection_name}[{index}] for {scenario.scenario_id}:{case.size} must be a JSON object"
                )
            if collection_name == "other_contracts":
                script_ref = item.get("script")
                if script_ref is not None:
                    if not isinstance(script_ref, str):
                        raise ManifestError("other_contracts script reference must be a string")
                    _contained_file(root, script_ref, f"{scenario.scenario_id}:{case.size} other_contracts script")
                continue
            map_id = item.get("id")
            if not isinstance(map_id, str) or not map_id.isdecimal() or int(map_id) < 1:
                raise ManifestError(
                    f"extra_big_maps[{index}].id must be a positive decimal string"
                )
            type_script = item.get("type_script")
            if type_script is not None:
                if not isinstance(type_script, str):
                    raise ManifestError("extra_big_maps type_script must be a string")
                _contained_file(root, type_script, f"{scenario.scenario_id}:{case.size} big-map type_script")
                if type(item.get("type_index")) is not int or item["type_index"] < 0:
                    raise ManifestError("extra_big_maps type_index must be a non-negative integer")
            literal_ref = item.get("map_literal_file")
            if literal_ref is not None:
                if not isinstance(literal_ref, str):
                    raise ManifestError("extra_big_maps map_literal_file must be a string")
                _contained_file(root, literal_ref, f"{scenario.scenario_id}:{case.size} big-map map_literal_file")


def _validate_schema_v2_case(
    root: Path,
    scenario: ScenarioSpec,
    case: Any,
    runtime: RuntimeLock,
    script_path: Path,
) -> None:
    if case.size < 0:
        raise ManifestError(f"scenario {scenario.scenario_id} has a negative case size")
    if case.state_validity not in {"constructed_state", "single_script_sequence_validated"}:
        raise ManifestError(f"scenario {scenario.scenario_id} has an unsupported state_validity")
    if case.expected_status not in {"success", "protocol_limit_exceeded"}:
        raise ManifestError(f"scenario {scenario.scenario_id}:{case.size} has an unsupported expected_status")

    resolved_values: dict[str, Any] = {}
    for field_name in ("storage_file", "input_file", "context_file"):
        reference = getattr(case, field_name)
        path = _contained_file(root, reference, f"{scenario.scenario_id}:{case.size} {field_name}")
        try:
            value = read_json(path)
        except (OSError, ValueError) as exc:
            raise ManifestError(f"invalid JSON in {field_name} for {scenario.scenario_id}:{case.size}: {exc}") from exc
        unsupported = _contains_unsupported_prim(value)
        if unsupported is not None:
            raise ManifestError(
                f"{scenario.scenario_id}:{case.size} {field_name} uses unsupported gas-sensitive instruction: {unsupported}"
            )
        resolved_values[field_name] = value

    _validate_schema_v2_context_references(
        root, scenario, case, resolved_values["context_file"]
    )
    try:
        context = ExecutionContext.from_dict(resolved_values["context_file"], runtime, root)
    except (TypeError, KeyError, ValueError, OSError) as exc:
        raise ManifestError(f"invalid execution context for {scenario.scenario_id}:{case.size}: {exc}") from exc
    if context.chain_id != runtime.chain_id:
        raise ManifestError(f"scenario {scenario.scenario_id}:{case.size} chain_id does not match runtime lock")

    if case.state_validity == "single_script_sequence_validated":
        if case.sequence_trace_file is None or case.sequence_index is None:
            raise ManifestError(
                f"{scenario.scenario_id}:{case.size} requires a sequence trace file and index"
            )
    elif case.sequence_trace_file is not None or case.sequence_index is not None:
        raise ManifestError(
            f"{scenario.scenario_id}:{case.size} has sequence metadata but is not sequence-validated"
        )

    if case.sequence_trace_file is None:
        return
    trace_path = _contained_file(
        root,
        case.sequence_trace_file,
        f"{scenario.scenario_id}:{case.size} sequence_trace_file",
    )
    try:
        trace = read_json(trace_path)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"invalid sequence trace for {scenario.scenario_id}:{case.size}: {exc}") from exc
    if not isinstance(trace, dict) or trace.get("schema_version") != 1:
        raise ManifestError(f"unsupported sequence trace for {scenario.scenario_id}:{case.size}")
    if trace.get("protocol") != runtime.protocol or trace.get("chain_id") != runtime.chain_id:
        raise ManifestError(f"sequence trace runtime does not match for {scenario.scenario_id}:{case.size}")
    if trace.get("script_sha256") != sha256_bytes(script_path.read_bytes()):
        raise ManifestError(f"sequence trace script hash does not match for {scenario.scenario_id}:{case.size}")
    snapshots = trace.get("snapshots")
    if not isinstance(snapshots, list) or not 0 <= case.sequence_index < len(snapshots):
        raise ManifestError(f"sequence trace index is invalid for {scenario.scenario_id}:{case.size}")
    snapshot = snapshots[case.sequence_index]
    if not isinstance(snapshot, dict) or snapshot.get("size") != case.size:
        raise ManifestError(f"sequence trace size does not match for {scenario.scenario_id}:{case.size}")
    expected_hashes = {
        "storage_sha256": sha256_json(resolved_values["storage_file"]),
        "input_sha256": sha256_json(resolved_values["input_file"]),
        "context_sha256": sha256_json(context.to_dict()),
    }
    if any(snapshot.get(key) != value for key, value in expected_hashes.items()):
        raise ManifestError(f"sequence trace fixture hashes do not match for {scenario.scenario_id}:{case.size}")


def load_manifest(path: Path, runtime: RuntimeLock) -> tuple[dict[str, Any], tuple[ScenarioSpec, ...], str]:
    data = read_json(path)
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or type(schema_version) is not int
        or schema_version not in {1, 2}
    ):
        raise ManifestError("manifest schema_version must be 1 or 2")
    if schema_version == 2:
        declared_runtime = data.get("runtime")
        if not isinstance(declared_runtime, dict):
            raise ManifestError("schema-v2 manifest requires a runtime lock")
        runtime_fields = (
            "octez_digest", "octez_version", "protocol", "chain_id", "hard_gas_limit_per_operation"
        )
        mismatch = [key for key in runtime_fields if declared_runtime.get(key) != runtime.to_dict().get(key)]
        if mismatch:
            raise ManifestError("manifest runtime does not match runtime lock: " + ", ".join(mismatch))
        if data.get("repetitions") != 2:
            raise ManifestError("schema-v2 manifest repetitions must be exactly 2")
    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ManifestError("manifest must contain a non-empty scenarios list")
    if any(not isinstance(item, dict) for item in raw_scenarios):
        raise ManifestError("each scenario must be a JSON object")
    try:
        scenarios = tuple(ScenarioSpec.from_dict(item, schema_version) for item in raw_scenarios)
    except (TypeError, KeyError, ValueError) as exc:
        raise ManifestError(f"scenario definition is invalid: {exc}") from exc
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ManifestError("scenario ids must be unique")
    for scenario in scenarios:
        if schema_version == 2:
            if not scenario.cases:
                raise ManifestError(f"scenario {scenario.scenario_id} must contain explicit cases")
            if len(scenario.sizes) != len(set(scenario.sizes)):
                raise ManifestError(f"scenario {scenario.scenario_id} case sizes must be unique")
            if tuple(sorted(scenario.sizes)) != scenario.sizes:
                raise ManifestError(f"scenario {scenario.scenario_id} case sizes must be in ascending order")
            if len(scenario.cases) != len(scenario.sizes):
                raise ManifestError(f"scenario {scenario.scenario_id} contains duplicate case sizes")
            if scenario.gas_cap is not None:
                raise ManifestError("schema-v2 scenarios use measurement_cap; gas_cap is legacy-only")
            if scenario.expected != "success":
                raise ManifestError(f"scenario {scenario.scenario_id} expected must be 'success'")
            if scenario.measurement_cap is not None and not 1 <= scenario.measurement_cap <= runtime.hard_gas_limit_per_operation:
                raise ManifestError(
                    f"scenario {scenario.scenario_id} measurement_cap must be between 1 and "
                    f"{runtime.hard_gas_limit_per_operation}"
                )
        elif scenario.measurement_cap is not None or scenario.cases:
            raise ManifestError("schema-v1 manifests cannot contain schema-v2 case fields")
        if len(scenario.sizes) != len(set(scenario.sizes)):
            raise ManifestError(f"scenario {scenario.scenario_id} sizes must be unique")
        if tuple(sorted(scenario.sizes)) != scenario.sizes:
            raise ManifestError(f"scenario {scenario.scenario_id} sizes must be in ascending order")
        if scenario.expected != "success":
            raise ManifestError(
                f"scenario {scenario.scenario_id} has unsupported expected value: {scenario.expected}"
            )
        if scenario.gas_cap is not None and not 1 <= scenario.gas_cap <= runtime.hard_gas_limit_per_operation:
            raise ManifestError(
                f"scenario {scenario.scenario_id} gas_cap must be between 1 and "
                f"{runtime.hard_gas_limit_per_operation}"
            )
        if schema_version == 2:
            script_path = _contained_file(
                path.parent, scenario.script, f"scenario {scenario.scenario_id} script"
            )
        else:
            script_path = (path.parent / scenario.script).resolve()
            if not script_path.is_file():
                raise ManifestError(
                    f"scenario {scenario.scenario_id} script does not exist: {script_path}"
                )
        try:
            script = load_script(script_path)
        except (OSError, ValueError) as exc:
            raise ManifestError(f"scenario {scenario.scenario_id} script is invalid: {exc}") from exc
        unsupported = _contains_unsupported_prim(script)
        if unsupported is not None:
            raise ManifestError(
                f"scenario {scenario.scenario_id} uses unsupported gas-sensitive instruction: {unsupported}"
            )
        for template_ref in (scenario.storage_template_file, scenario.input_template_file):
            if template_ref is not None:
                template_path = path.parent / template_ref
                if not template_path.is_file():
                    raise ManifestError(
                        f"scenario {scenario.scenario_id} template does not exist: {template_path}"
                    )
        if scenario.storage_generator == "template" and scenario.storage_template is None and scenario.storage_template_file is None:
            raise ManifestError(
                f"scenario {scenario.scenario_id} template storage generator requires a template"
            )
        if scenario.input_generator == "template" and scenario.input_template is None and scenario.input_template_file is None:
            raise ManifestError(
                f"scenario {scenario.scenario_id} template input generator requires a template"
            )
        context = ExecutionContext.from_dict(scenario.context, runtime, path.parent)
        unsupported = _contains_unsupported_prim(context.to_dict())
        if unsupported is not None:
            raise ManifestError(
                f"scenario {scenario.scenario_id} context uses unsupported gas-sensitive instruction: {unsupported}"
            )
        for template in (scenario.storage_template, scenario.input_template):
            unsupported = _contains_unsupported_prim(template)
            if unsupported is not None:
                raise ManifestError(
                    f"scenario {scenario.scenario_id} template uses unsupported gas-sensitive instruction: {unsupported}"
                )
        for template_ref in (scenario.storage_template_file, scenario.input_template_file):
            if template_ref is not None:
                template_path = path.parent / template_ref
                try:
                    template_data = read_json(template_path)
                except (OSError, ValueError) as exc:
                    raise ManifestError(f"scenario {scenario.scenario_id} template is invalid: {exc}") from exc
                unsupported = _contains_unsupported_prim(template_data)
                if unsupported is not None:
                    raise ManifestError(
                        f"scenario {scenario.scenario_id} template uses unsupported gas-sensitive instruction: {unsupported}"
                    )
        if context.chain_id != runtime.chain_id:
            raise ManifestError(
                f"scenario {scenario.scenario_id} chain_id does not match runtime lock"
            )
        if schema_version == 2:
            for case in scenario.cases:
                _validate_schema_v2_case(path.parent, scenario, case, runtime, script_path)
    return data, scenarios, sha256_json(data)


def load_script(path: Path) -> Any:
    try:
        script = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"script is not Micheline JSON: {path}: {exc}") from exc
    if isinstance(script, dict) and isinstance(script.get("code"), list):
        script = script["code"]
    if not isinstance(script, list):
        raise ManifestError(f"script must be a Micheline sequence: {path}")
    prims = {item.get("prim") for item in script if isinstance(item, dict)}
    if not {"parameter", "storage", "code"}.issubset(prims):
        raise ManifestError(f"script must contain parameter, storage and code: {path}")
    return script
