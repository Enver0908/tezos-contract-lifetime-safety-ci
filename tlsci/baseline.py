from __future__ import annotations

from pathlib import Path

from .models import Baseline, Measurement, RuntimeLock
from .util import read_json, write_json


def build_baseline(
    runtime: RuntimeLock,
    measurements: list[Measurement],
    schema_version: int = 3,
) -> Baseline:
    if not measurements:
        raise ValueError("baseline cannot be empty")
    scenarios: dict[str, dict[str, object]] = {}
    for measurement in measurements:
        if measurement.status != "success" or measurement.minimum_successful_gas_budget is None:
            raise ValueError("baseline can only contain successful measurements")
        key = f"{measurement.scenario_id}:{measurement.size}"
        if key in scenarios:
            raise ValueError(f"duplicate baseline key: {key}")
        scenarios[key] = measurement.to_dict()
    if schema_version not in {2, 3}:
        raise ValueError("baseline schema_version must be 2 or 3")
    return Baseline(schema_version=schema_version, runtime=runtime.to_dict(), scenarios=scenarios)


def save_baseline(path: Path, baseline: Baseline) -> None:
    if baseline.schema_version not in {2, 3}:
        raise ValueError("only schema-v2 or schema-v3 baselines may be written")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite baseline: {path}")
    write_json(path, {
        "schema_version": baseline.schema_version,
        "runtime": baseline.runtime,
        "scenarios": baseline.scenarios,
    })


def load_baseline(path: Path) -> Baseline:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("baseline must contain a JSON object")
    baseline = Baseline.from_dict(data)
    if not isinstance(baseline.runtime, dict) or not isinstance(baseline.scenarios, dict):
        raise ValueError("baseline runtime and scenarios must be JSON objects")
    if not baseline.scenarios:
        raise ValueError("baseline cannot be empty")
    for key, entry in baseline.scenarios.items():
        if not isinstance(entry, dict):
            raise ValueError(f"baseline entry {key} must be a JSON object")
        if entry.get("cap_kind") not in {"policy", "protocol", "search"}:
            raise ValueError(f"baseline entry {key} has an invalid or missing cap_kind")
        if not entry.get("measurement_method"):
            raise ValueError(f"baseline entry {key} is missing measurement_method")
        try:
            gas = int(entry["minimum_successful_gas_budget"])
            cap = int(entry["gas_cap"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"baseline entry {key} has invalid gas bounds") from exc
        if gas < 1 or cap < gas:
            raise ValueError(f"baseline entry {key} has impossible gas bounds")
        if not entry.get("fixture_sha256") or not entry.get("code_sha256"):
            raise ValueError(f"baseline entry {key} is missing fixture or code provenance")
    return baseline
