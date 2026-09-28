from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from .models import ExecutionContext, RuntimeLock, ScenarioSpec
from .util import sha256_bytes


def _nat(value: int) -> dict[str, str]:
    return {"int": str(value)}


def _bytes(value: str) -> dict[str, str]:
    return {"bytes": value}


def _string(value: str) -> dict[str, str]:
    return {"string": value}


def _address(value: str) -> dict[str, str]:
    return _string(value)


def _base58check(prefix: bytes, payload: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    body = prefix + payload
    checksum = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    raw = body + checksum
    leading_zeroes = len(raw) - len(raw.lstrip(b"\0"))
    value = int.from_bytes(raw, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = alphabet[remainder] + encoded
    return alphabet[0] * leading_zeroes + encoded


def _address_for_index(index: int) -> str:
    if index < 0:
        raise ValueError("address index must be non-negative")
    if index == 0:
        return "tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv"
    payload = hashlib.sha256(f"tlsci-confirmatory-key-{index}".encode("ascii")).digest()[:20]
    return _base58check(bytes((6, 161, 159)), payload)


def _elt(key: Any, value: Any) -> dict[str, Any]:
    return {"prim": "Elt", "args": [key, value]}


def generate_big_map_literal(name: str, size: int) -> list[dict[str, Any]]:
    if size < 0:
        raise ValueError("size must be non-negative")
    if name == "nat_nat":
        return [_elt(_nat(index), _nat(1)) for index in range(size)]
    if name == "pair_nat_nat_nat":
        return [
            _elt({"prim": "Pair", "args": [_nat(index), _nat(0)]}, _nat(1))
            for index in range(size)
        ]
    if name == "address_nat":
        entries = [
            _elt(_address(_address_for_index(index)), _nat(1))
            for index in range(size)
        ]
        return sorted(entries, key=lambda entry: entry["args"][0]["string"])
    if name == "pair_address_address_nat":
        default_key = _address("tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv")
        entries = [
            _elt(
                {"prim": "Pair", "args": [_address(_address_for_index(index)), default_key]},
                _nat(1),
            )
            for index in range(size)
        ]
        return sorted(entries, key=lambda entry: entry["args"][0]["args"][0]["string"])
    raise ValueError(f"unknown big-map literal generator: {name}")


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
    if name == "bounded_descending_list_nat":
        limit = min(size, 64)
        return [_nat(value) for value in range(limit, 0, -1)]
    if name == "descending_list_nat":
        return [_nat(value) for value in range(size, 0, -1)]
    if name == "map_nat_nat":
        return [_elt(_nat(index), _nat(1)) for index in range(size)]
    if name == "big_map_nat_nat":
        return [_elt(_nat(index), _nat(1)) for index in range(size)]
    if name == "lookup_pair_map_nat_nat":
        values = [_elt(_nat(index), _nat(1)) for index in range(size)]
        return {"prim": "Pair", "args": [values, _nat(0)]}
    if name == "lookup_pair_big_map_nat_nat":
        return {"prim": "Pair", "args": [{"int": "0"}, _nat(0)]}
    if name == "lookup_pair_map_address_nat":
        values = [
            _elt(_address(_address_for_index(index)), _nat(1))
            for index in range(size)
        ]
        values.sort(key=lambda entry: entry["args"][0]["string"])
        return {"prim": "Pair", "args": [values, _nat(0)]}
    if name == "lookup_pair_big_map_address_nat":
        return {
            "prim": "Pair",
            "args": [generate_big_map_literal("address_nat", size), _nat(0)],
        }
    if name == "lookup_nested_allowances":
        nested = [
            _elt(_nat(owner), [_elt(_nat(0), _nat(1))])
            for owner in range(size)
        ]
        return {"prim": "Pair", "args": [nested, _nat(0)]}
    if name == "lookup_nested_address_allowances":
        spender = _address("tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv")
        nested = [
            _elt(
                _address(_address_for_index(owner)),
                [_elt(spender, _nat(1))],
            )
            for owner in range(size)
        ]
        nested.sort(key=lambda entry: entry["args"][0]["string"])
        return {"prim": "Pair", "args": [nested, _nat(0)]}
    if name == "lookup_pair_big_map_pair_nat_nat_nat":
        return {"prim": "Pair", "args": [{"int": "1"}, _nat(0)]}
    if name == "lookup_pair_big_map_pair_address_address_nat":
        return {
            "prim": "Pair",
            "args": [generate_big_map_literal("pair_address_address_nat", size), _nat(0)],
        }
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


@dataclass(frozen=True)
class ResolvedCase:
    storage: Any
    input_value: Any
    context: ExecutionContext
    state_validity: str
    sequence_trace_sha256: str | None = None


def resolve_case(
    scenario: ScenarioSpec,
    size: int,
    manifest_dir: Path,
    runtime: RuntimeLock,
) -> ResolvedCase:
    if scenario.manifest_schema_version == 1:
        storage, input_value = generate_case(scenario, size, manifest_dir)
        context = ExecutionContext.from_dict(scenario.context, runtime, manifest_dir)
        if scenario.entrypoint is not None:
            context = replace(context, entrypoint=scenario.entrypoint)
        return ResolvedCase(storage, input_value, context, "constructed_state")

    case = next((item for item in scenario.cases if item.size == size), None)
    if case is None:
        raise ValueError(f"no explicit case for {scenario.scenario_id}:{size}")
    storage = json.loads((manifest_dir / case.storage_file).read_text(encoding="utf-8"))
    input_value = json.loads((manifest_dir / case.input_file).read_text(encoding="utf-8"))
    context_data = json.loads((manifest_dir / case.context_file).read_text(encoding="utf-8"))
    context = ExecutionContext.from_dict(context_data, runtime, manifest_dir)
    if scenario.entrypoint is not None:
        context = replace(context, entrypoint=scenario.entrypoint)
    trace_hash = None
    if case.sequence_trace_file is not None:
        trace_hash = sha256_bytes((manifest_dir / case.sequence_trace_file).read_bytes())
    return ResolvedCase(storage, input_value, context, case.state_validity, trace_hash)
