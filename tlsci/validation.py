from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ExecutionContext, RuntimeLock, ScenarioSpec
from .util import read_json, sha256_json


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


def load_manifest(path: Path, runtime: RuntimeLock) -> tuple[dict[str, Any], tuple[ScenarioSpec, ...], str]:
    data = read_json(path)
    if not isinstance(data, dict) or int(data.get("schema_version", 0)) != 1:
        raise ManifestError("manifest schema_version must be 1")
    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ManifestError("manifest must contain a non-empty scenarios list")
    scenarios = tuple(ScenarioSpec.from_dict(item) for item in raw_scenarios)
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ManifestError("scenario ids must be unique")
    for scenario in scenarios:
        if len(scenario.sizes) != len(set(scenario.sizes)):
            raise ManifestError(f"scenario {scenario.scenario_id} sizes must be unique")
        if scenario.expected != "success":
            raise ManifestError(
                f"scenario {scenario.scenario_id} has unsupported expected value: {scenario.expected}"
            )
        if scenario.gas_cap is not None and not 1 <= scenario.gas_cap <= runtime.hard_gas_limit_per_operation:
            raise ManifestError(
                f"scenario {scenario.scenario_id} gas_cap must be between 1 and "
                f"{runtime.hard_gas_limit_per_operation}"
            )
        script_path = path.parent / scenario.script
        if not script_path.is_file():
            raise ManifestError(f"scenario {scenario.scenario_id} script does not exist: {script_path}")
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
