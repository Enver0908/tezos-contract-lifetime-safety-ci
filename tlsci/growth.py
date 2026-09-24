from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


def _nat(value: int) -> dict[str, str]:
    return {"int": str(value)}


def _bytes(value: str) -> dict[str, str]:
    return {"bytes": value}


def _string(value: str) -> dict[str, str]:
    return {"string": value}


def _address(value: str) -> dict[str, str]:
    return _string(value)


def _elt(key: Any, value: Any) -> dict[str, Any]:
    return {"prim": "Elt", "args": [key, value]}


def _repeat(value: Any, size: int) -> list[Any]:
    return [deepcopy(value) for _ in range(size)]


def generate_storage(name: str, size: int, template: Any | None = None) -> Any:
    if size < 0:
        raise ValueError("size must be non-negative")
    if name == "unit":
        return {"prim": "Unit"}
    if name in {"list_nat", "bounded_list_nat"}:
        limit = 64 if name == "bounded_list_nat" else size
        return _repeat(_nat(1), min(size, limit))
    if name == "map_nat_nat":
        return [_elt(_nat(index), _nat(1)) for index in range(size)]
    if name == "big_map_nat_nat":
        return [_elt(_nat(index), _nat(1)) for index in range(size)]
    if name == "bytes":
        return _bytes("aa" * size)
    if name == "nat":
        return _nat(size)
    if name == "template":
        if template is None:
            raise ValueError("template storage generator requires storage_template")
        return materialize_template(template, size)
    raise ValueError(f"unknown storage generator: {name}")


def generate_input(name: str, size: int, template: Any | None = None) -> Any:
    if name == "unit":
        return {"prim": "Unit"}
    if name == "nat":
        return _nat(size)
    if name == "bytes32":
        return _bytes((f"{size:064x}")[-64:])
    if name == "template":
        if template is None:
            raise ValueError("template input generator requires input_template")
        return materialize_template(template, size)
    raise ValueError(f"unknown input generator: {name}")


def materialize_template(value: Any, size: int) -> Any:
    if isinstance(value, str):
        return value.replace("{{size}}", str(size))
    if isinstance(value, list):
        return [materialize_template(item, size) for item in value]
    if isinstance(value, dict):
        return {key: materialize_template(item, size) for key, item in value.items()}
    return value


def _template_from_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def generate_case(scenario: Any, size: int, manifest_dir: Path | None = None) -> tuple[Any, Any]:
    storage_template = getattr(scenario, "storage_template", None)
    input_template = getattr(scenario, "input_template", None)
    storage_template_file = getattr(scenario, "storage_template_file", None)
    input_template_file = getattr(scenario, "input_template_file", None)
    if storage_template_file is not None:
        if manifest_dir is None:
            raise ValueError("storage_template_file requires manifest directory")
        storage_path = Path(storage_template_file)
        if not storage_path.is_absolute():
            storage_path = manifest_dir / storage_path
        storage_template = _template_from_file(storage_path.resolve())
    if input_template_file is not None:
        if manifest_dir is None:
            raise ValueError("input_template_file requires manifest directory")
        input_path = Path(input_template_file)
        if not input_path.is_absolute():
            input_path = manifest_dir / input_path
        input_template = _template_from_file(input_path.resolve())
    storage = generate_storage(scenario.storage_generator, size, storage_template)
    input_value = generate_input(scenario.input_generator, size, input_template)
    return storage, input_value
