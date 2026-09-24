from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import sha256_bytes, sha256_json, write_json


def provenance_for_file(path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_bytes(path.read_bytes()),
        **extra,
    }


def scenario_fingerprint(scenario: Any, runtime: Any) -> str:
    return sha256_json({
        "scenario_id": scenario.scenario_id,
        "sizes": list(scenario.sizes),
        "storage_generator": scenario.storage_generator,
        "input_generator": scenario.input_generator,
        "expected": scenario.expected,
        "context": scenario.context,
        "runtime_protocol": runtime.protocol,
    })
