from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _load_script_code(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("code"), list):
        data = data["code"]
    if not isinstance(data, list):
        raise ValueError(f"script reference must contain a Micheline sequence: {path}")
    return data


def _parameter_type_from_script(path: Path) -> Any:
    code = _load_script_code(path)
    for item in code:
        if isinstance(item, dict) and item.get("prim") == "parameter":
            args = item.get("args") or []
            if len(args) == 1:
                return args[0]
    raise ValueError(f"script reference has no parameter declaration: {path}")


def _storage_type_from_script(path: Path) -> Any:
    code = _load_script_code(path)
    for item in code:
        if isinstance(item, dict) and item.get("prim") == "storage":
            args = item.get("args") or []
            if len(args) == 1:
                return args[0]
    raise ValueError(f"script reference has no storage declaration: {path}")


def _top_level_big_map_types(type_expr: Any) -> list[Any]:
    if not isinstance(type_expr, dict):
        return []
    if type_expr.get("prim") == "big_map":
        return [type_expr]
    if type_expr.get("prim") == "lambda":
        return []
    result: list[Any] = []
    for arg in type_expr.get("args", ()):
        result.extend(_top_level_big_map_types(arg))
    return result


@dataclass(frozen=True)
class RuntimeLock:
    schema_version: int
    octez_image: str
    octez_digest: str
    octez_version: str
    protocol: str
    chain_id: str
    hard_gas_limit_per_operation: int
    source: str = "local-runtime-lock"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeLock":
        return cls(
            schema_version=int(data["schema_version"]),
            octez_image=str(data["octez_image"]),
            octez_digest=str(data["octez_digest"]),
            octez_version=str(data["octez_version"]),
            protocol=str(data["protocol"]),
            chain_id=str(data["chain_id"]),
            hard_gas_limit_per_operation=int(data["hard_gas_limit_per_operation"]),
            source=str(data.get("source", "local-runtime-lock")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class ExecutionContext:
    chain_id: str
    source: str = "tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv"
    payer: str | None = None
    amount: str = "0"
    balance: str = "1000000"
    now: str | None = None
    level: int | None = None
    entrypoint: str | None = None
    other_contracts: tuple[dict[str, Any], ...] = ()
    extra_big_maps: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        runtime: RuntimeLock,
        manifest_dir: Path | None = None,
    ) -> "ExecutionContext":
        resolved_other_contracts: list[dict[str, Any]] = []
        for item in data.get("other_contracts", ()):
            contract = dict(item)
            script_ref = contract.pop("script", None)
            if script_ref is not None:
                if manifest_dir is None:
                    raise ValueError("other_contracts script reference requires manifest directory")
                script_path = Path(script_ref)
                if not script_path.is_absolute():
                    script_path = manifest_dir / script_path
                contract["type"] = _parameter_type_from_script(script_path.resolve())
            if "address" not in contract or "type" not in contract:
                raise ValueError("each other_contracts item requires address and type or script")
            resolved_other_contracts.append(contract)
        resolved_extra_big_maps: list[dict[str, Any]] = []
        for item in data.get("extra_big_maps", ()):
            big_map = dict(item)
            type_script_ref = big_map.pop("type_script", None)
            type_index = big_map.pop("type_index", None)
            if type_script_ref is not None:
                if manifest_dir is None:
                    raise ValueError("type_script requires manifest directory")
                type_script_path = Path(type_script_ref)
                if not type_script_path.is_absolute():
                    type_script_path = manifest_dir / type_script_path
                big_map_type_candidates = _top_level_big_map_types(
                    _storage_type_from_script(type_script_path.resolve())
                )
                if type_index is None:
                    raise ValueError("type_script requires type_index")
                try:
                    selected_type = big_map_type_candidates[int(type_index)]
                except (IndexError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"type_index {type_index} is not available in {type_script_path}"
                    ) from exc
                big_map.setdefault("key_type", selected_type["args"][0])
                big_map.setdefault("val_type", selected_type["args"][1])
            literal_ref = big_map.pop("map_literal_file", None)
            if literal_ref is not None:
                if manifest_dir is None:
                    raise ValueError("map_literal_file requires manifest directory")
                literal_path = Path(literal_ref)
                if not literal_path.is_absolute():
                    literal_path = manifest_dir / literal_path
                literal_data = json.loads(literal_path.resolve().read_text(encoding="utf-8"))
                if isinstance(literal_data, dict) and "map_literal" in literal_data:
                    literal_data = literal_data["map_literal"]
                if not isinstance(literal_data, list):
                    raise ValueError(f"map_literal_file must contain a Micheline sequence: {literal_path}")
                big_map["map_literal"] = literal_data
            if "id" not in big_map or "key_type" not in big_map or "val_type" not in big_map:
                raise ValueError("each extra_big_maps item requires id, key_type and val_type")
            big_map.setdefault("map_literal", [])
            resolved_extra_big_maps.append(big_map)
        return cls(
            chain_id=str(data.get("chain_id", runtime.chain_id)),
            source=str(data.get("source", "tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv")),
            payer=data.get("payer"),
            amount=str(data.get("amount", "0")),
            balance=str(data.get("balance", "1000000")),
            now=data.get("now"),
            level=int(data["level"]) if data.get("level") is not None else None,
            entrypoint=data.get("entrypoint"),
            other_contracts=tuple(resolved_other_contracts),
            extra_big_maps=tuple(resolved_extra_big_maps),
        )

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(asdict(self))
        data["other_contracts"] = list(self.other_contracts)
        data["extra_big_maps"] = list(self.extra_big_maps)
        return data


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    script: str
    sizes: tuple[int, ...]
    storage_generator: str
    input_generator: str
    expected: str
    gas_cap: int | None = None
    entrypoint: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    storage_template: Any | None = None
    input_template: Any | None = None
    storage_template_file: str | None = None
    input_template_file: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioSpec":
        sizes = tuple(int(size) for size in data.get("sizes", ()))
        if not sizes or any(size < 0 for size in sizes):
            raise ValueError(f"scenario {data.get('id')} must contain non-negative sizes")
        return cls(
            scenario_id=str(data["id"]),
            script=str(data["script"]),
            sizes=sizes,
            storage_generator=str(data.get("storage_generator", "none")),
            input_generator=str(data.get("input_generator", "unit")),
            expected=str(data.get("expected", "success")),
            gas_cap=int(data["gas_cap"]) if data.get("gas_cap") is not None else None,
            entrypoint=data.get("entrypoint"),
            context=dict(data.get("context", {})),
            storage_template=data.get("storage_template"),
            input_template=data.get("input_template"),
            storage_template_file=data.get("storage_template_file"),
            input_template_file=data.get("input_template_file"),
        )


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    gas_budget: int
    storage: Any | None = None
    operations: list[Any] = field(default_factory=list)
    lazy_storage_diff: Any | None = None
    error_ids: tuple[str, ...] = ()
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    raw_stdout_tail: str = ""
    raw_stderr_tail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(asdict(self))
        data["error_ids"] = list(self.error_ids)
        return data


@dataclass(frozen=True)
class Measurement:
    scenario_id: str
    size: int
    status: str
    minimum_successful_gas_budget: int | None
    last_failed_gas_budget: int | None
    first_successful_gas_budget: int | None
    gas_cap: int
    storage_bytes: int | None
    operations_count: int | None
    fixture_sha256: str
    code_sha256: str
    measurement_method: str = "integer_budget_search"
    execution_scope: str = "single_script"
    internal_operations_executed: bool = False
    error_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(asdict(self))
        data["error_ids"] = list(self.error_ids)
        return data


@dataclass(frozen=True)
class Baseline:
    schema_version: int
    runtime: dict[str, Any]
    scenarios: dict[str, dict[str, Any]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Baseline":
        return cls(
            schema_version=int(data["schema_version"]),
            runtime=dict(data["runtime"]),
            scenarios=dict(data["scenarios"]),
        )


@dataclass(frozen=True)
class PolicyDecision:
    scenario_id: str
    size: int
    decision: str
    rule_ids: tuple[str, ...]
    old_gas: int | None
    new_gas: int | None
    delta_gas: int | None
    delta_percent: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(asdict(self))
        data["rule_ids"] = list(self.rule_ids)
        return data


@dataclass
class RunReport:
    schema_version: int
    generated_at_utc: str
    runtime: dict[str, Any]
    manifest_sha256: str
    measurements: list[Measurement]
    decisions: list[PolicyDecision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "runtime": _jsonable(self.runtime),
            "manifest_sha256": self.manifest_sha256,
            "measurements": [measurement.to_dict() for measurement in self.measurements],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "errors": list(self.errors),
            "provenance": _jsonable(self.provenance),
        }
