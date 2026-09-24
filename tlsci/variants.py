from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .util import read_json, sha256_bytes, write_json


def make_noop_gas_variant(source: Path, output: Path, repetitions: int) -> dict[str, Any]:
    if repetitions <= 0 or repetitions > 1000:
        raise ValueError("repetitions must be between 1 and 1000")
    data = json.loads(source.read_text(encoding="utf-8"))
    code = data.get("code") if isinstance(data, dict) else data
    if not isinstance(code, list):
        raise ValueError("source script must contain a Micheline sequence")
    variant = deepcopy(code)
    code_declaration = next(
        (item for item in variant if isinstance(item, dict) and item.get("prim") == "code"),
        None,
    )
    if not isinstance(code_declaration, dict) or not code_declaration.get("args"):
        raise ValueError("source script has no code declaration")
    body = code_declaration["args"][0]
    if not isinstance(body, list):
        raise ValueError("source script code declaration has no instruction sequence")
    no_op = [
        {"prim": "PUSH", "args": [{"prim": "nat"}, {"int": "0"}]},
        {"prim": "DROP"},
    ]
    code_declaration["args"][0] = no_op * repetitions + body
    write_json(output, variant)
    return {
        "source": str(source),
        "output": str(output),
        "repetitions": repetitions,
        "source_sha256": sha256_bytes(source.read_bytes()),
        "output_sha256": sha256_bytes(output.read_bytes()),
        "mutation": "prepend PUSH nat 0; DROP repetitions times",
    }


def make_controlled_manifest(source_manifest: Path, output_manifest: Path, repetitions: int) -> dict[str, Any]:
    data = read_json(source_manifest)
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("source manifest must contain a scenarios list")
    updated = deepcopy(data)
    updated["name"] = f"{data.get('name', 'manifest')}-controlled-regression"
    updated["controlled_mutation"] = {
        "repetitions": repetitions,
        "mutation": "prepend PUSH nat 0; DROP repetitions times to each script code",
        "semantic_intent": "preserve the original entrypoint result while increasing execution cost",
    }
    for scenario in updated["scenarios"]:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("script"), str):
            raise ValueError("each manifest scenario must contain a script path")
        source = (source_manifest.parent / scenario["script"]).resolve()
        variant_name = f"{source.stem}.controlled-{repetitions}{source.suffix}"
        variant = source.with_name(variant_name)
        make_noop_gas_variant(source, variant, repetitions)
        scenario["script"] = str(variant.relative_to(source_manifest.parent.resolve()))
    write_json(output_manifest, updated)
    return {
        "source_manifest": str(source_manifest),
        "output_manifest": str(output_manifest),
        "repetitions": repetitions,
        "scenarios": len(updated["scenarios"]),
    }
