from __future__ import annotations

from pathlib import Path

from .models import Baseline, Measurement, RuntimeLock
from .util import read_json, write_json


def build_baseline(runtime: RuntimeLock, measurements: list[Measurement]) -> Baseline:
    scenarios: dict[str, dict[str, object]] = {}
    for measurement in measurements:
        if measurement.status != "success" or measurement.minimum_successful_gas_budget is None:
            raise ValueError("baseline can only contain successful measurements")
        key = f"{measurement.scenario_id}:{measurement.size}"
        if key in scenarios:
            raise ValueError(f"duplicate baseline key: {key}")
        scenarios[key] = measurement.to_dict()
    return Baseline(schema_version=1, runtime=runtime.to_dict(), scenarios=scenarios)


def save_baseline(path: Path, baseline: Baseline) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite baseline: {path}")
    write_json(path, {
        "schema_version": baseline.schema_version,
        "runtime": baseline.runtime,
        "scenarios": baseline.scenarios,
    })


def load_baseline(path: Path) -> Baseline:
    return Baseline.from_dict(read_json(path))
