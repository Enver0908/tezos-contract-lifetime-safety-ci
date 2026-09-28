from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tlsci.growth import (
    _address_for_index,
    _elt,
    _nat,
    generate_big_map_literal,
    generate_input,
    generate_storage,
)
from tlsci.models import ExecutionContext, RuntimeLock
from tlsci.octez import OctezRunner
from tlsci.runtime import load_runtime
from tlsci.util import canonical_json, read_json, sha256_bytes, sha256_json, write_json
from tlsci.validation import load_manifest


SIZES = (0, 1, 8, 64, 256, 1024, 4096)
SNAPSHOT_SIZES = (0, 1, 8, 64, 256)
PROJECT = Path(__file__).resolve().parents[1]
TARGET = PROJECT / "fixtures" / "growth-v2"
SOURCE_EVIDENCE = PROJECT.parent.parent / "outputs" / "evidence" / "integrations"
RECIPIENT = "tz1VSUr8wwNhLAzempoch5d6hLRiTh8Cjcjb"
DEFAULT_ADDRESS = "tz1ddb9NMYHZi5UzPdzTZMYQQZoMub195zgv"


def _right(value: Any) -> dict[str, Any]:
    return {"prim": "Right", "args": [value]}


def _pair(*values: Any) -> dict[str, Any]:
    return {"prim": "Pair", "args": list(values)}


def _address(value: str) -> dict[str, str]:
    return {"string": value}


def _map_value(entries: list[dict[str, Any]], key: Any) -> Any | None:
    canonical = canonical_json(key)
    for entry in entries:
        if entry.get("prim") == "Elt" and canonical_json(entry["args"][0]) == canonical:
            return entry["args"][1]
    return None


def _sort_key(value: Any) -> Any:
    if isinstance(value, dict) and "int" in value:
        return (0, int(value["int"]))
    if isinstance(value, dict) and "string" in value:
        return (1, value["string"])
    if isinstance(value, dict) and value.get("prim") == "Pair":
        return (2, tuple(_sort_key(item) for item in value.get("args", ())))
    return (3, canonical_json(value))


def _apply_lazy_diff(big_maps: dict[str, list[dict[str, Any]]], diff: Any) -> None:
    if diff is None:
        return
    if not isinstance(diff, list):
        raise ValueError("Octez returned a non-list lazy_storage_diff")
    for item in diff:
        if not isinstance(item, dict) or item.get("kind") != "big_map":
            continue
        map_id = str(item.get("id"))
        if map_id not in big_maps:
            raise ValueError(f"Octez updated undeclared big_map id {map_id}")
        change = item.get("diff")
        if not isinstance(change, dict) or change.get("action") != "update":
            raise ValueError(f"unsupported big_map diff action for id {map_id}")
        updates = change.get("updates")
        if not isinstance(updates, list):
            raise ValueError(f"big_map diff for id {map_id} has no updates list")
        entries = big_maps[map_id]
        for update in updates:
            if not isinstance(update, dict) or "key" not in update:
                raise ValueError(f"big_map diff for id {map_id} lacks an explicit key")
            key_hash = canonical_json(update["key"])
            entries[:] = [
                entry for entry in entries
                if canonical_json(entry["args"][0]) != key_hash
            ]
            if update.get("value") is not None:
                entries.append(_elt(update["key"], update["value"]))
        entries.sort(key=lambda entry: _sort_key(entry["args"][0]))


def _copy_inputs(stage: Path) -> dict[str, dict[str, Any]]:
    contracts = stage / "contracts"
    contracts.mkdir(parents=True)
    templates = stage / "templates"
    templates.mkdir(parents=True)
    proposal_template = PROJECT / "fixtures" / "growth-v2" / "templates" / "tzsafe-sign-proposal-content.json"
    if not proposal_template.is_file():
        raise FileNotFoundError(f"pinned TzSafe proposal-content template is missing: {proposal_template}")
    shutil.copyfile(proposal_template, templates / proposal_template.name)
    refs: dict[str, dict[str, Any]] = {}
    tracked_corpus = PROJECT / "fixtures" / "growth-v2"
    tracked_sources = tracked_corpus / "sources.json"
    use_tracked = (tracked_sources.is_file() and (tracked_corpus / "contracts" / "tzsafe.json").is_file())
    if use_tracked:
        tracked_metadata = read_json(tracked_sources)
        refs = tracked_metadata["upstream_contracts"]
    mappings = (
        ("tzsafe", "tzsafe-sign-proposal"),
        ("fa2", "fa2-single-asset-transfer"),
    )
    for contract_id, source_dir in mappings:
        if use_tracked:
            source_contract = tracked_corpus / "contracts" / f"{contract_id}.json"
            license_name = refs[contract_id]["license_file"]
            source_license = tracked_corpus / "contracts" / license_name
            if not source_license.is_file():
                raise FileNotFoundError(f"pinned upstream license notice is missing: {source_license}")
            if sha256_bytes(source_contract.read_bytes()) != refs[contract_id]["compiled_michelson_sha256"]:
                raise ValueError(f"compiled Michelson hash differs from pinned source metadata for {contract_id}")
            shutil.copyfile(source_contract, contracts / source_contract.name)
            shutil.copyfile(source_license, contracts / source_license.name)
        else:
            source = SOURCE_EVIDENCE / source_dir
            destination = contracts / f"{contract_id}.json"
            shutil.copyfile(source / "contract.json", destination)
            license_path = contracts / f"{contract_id}-UPSTREAM-LICENSE.txt"
            shutil.copyfile(source / "UPSTREAM-LICENSE.txt", license_path)
            source_manifest = read_json(source / "manifest.json")
            refs[contract_id] = {
                "reference": source_manifest["reference"],
                "compiled_michelson_sha256": sha256_bytes(destination.read_bytes()),
                "license_file": license_path.name,
            }
    synthetic = stage / "contracts" / "synthetic"
    synthetic.mkdir()
    for name, source in (
        ("unbounded_list_append.json", PROJECT / "fixtures" / "synthetic" / "list_append.json"),
        ("bounded_last64_append.json", PROJECT / "fixtures" / "strategy-v1" / "bounded_last64.json"),
        ("ordinary_map_lookup.json", PROJECT / "fixtures" / "strategy-v1" / "map_lookup.json"),
        ("lazy_big_map_lookup.json", PROJECT / "fixtures" / "strategy-v1" / "big_map_lookup.json"),
        ("nested_allowance_lookup.json", PROJECT / "fixtures" / "strategy-v1" / "nested_allowance_lookup.json"),
        ("flattened_allowance_lookup.json", PROJECT / "fixtures" / "strategy-v1" / "flat_allowance_lookup.json"),
    ):
        shutil.copyfile(source, synthetic / name)
    return refs


def _write_case_files(
    stage: Path,
    scenario_id: str,
    size: int,
    storage: Any,
    input_value: Any,
    context_data: dict[str, Any],
    runtime: RuntimeLock,
    sequence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_dir = stage / "cases" / scenario_id
    case_dir.mkdir(parents=True, exist_ok=True)
    storage_path = f"cases/{scenario_id}/{size}.storage.json"
    input_path = f"cases/{scenario_id}/{size}.input.json"
    context_path = f"cases/{scenario_id}/{size}.context.json"
    write_json(stage / storage_path, storage)
    write_json(stage / input_path, input_value)

    serialized_context = deepcopy(context_data)
    for index, big_map in enumerate(serialized_context.get("extra_big_maps", [])):
        literal = big_map.pop("_map_literal")
        literal_name = big_map.pop("_map_file")
        literal_path = f"maps/{literal_name}"
        write_json(stage / literal_path, literal)
        big_map["map_literal_file"] = literal_path
    write_json(stage / context_path, serialized_context)

    case: dict[str, Any] = {
        "size": size,
        "storage_file": storage_path,
        "input_file": input_path,
        "context_file": context_path,
        "state_validity": "constructed_state",
    }
    if sequence is not None:
        case.update(
            {
                "state_validity": "single_script_sequence_validated",
                "sequence_trace_file": sequence["trace_file"],
                "sequence_index": sequence["index"],
            }
        )
    return case


def _context_data(
    runtime: RuntimeLock,
    source: str,
    entrypoint: str | None,
    bigmaps: list[tuple[int, int, str, list[dict[str, Any]]]],
    now: str | None = None,
    level: int | None = None,
    type_script: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "chain_id": runtime.chain_id,
        "source": source,
        "amount": "0",
        "balance": "1000000",
        "extra_big_maps": [],
    }
    if entrypoint is not None:
        data["entrypoint"] = entrypoint
    if now is not None:
        data["now"] = now
    if level is not None:
        data["level"] = level
    if type_script is None:
        type_script = "contracts/tzsafe.json" if entrypoint in {"create_proposal", "sign_proposal"} else "contracts/fa2.json"
    for map_id, type_index, name, literal in bigmaps:
        data["extra_big_maps"].append(
            {
                "id": str(map_id),
                "type_script": type_script,
                "type_index": type_index,
                "_map_literal": deepcopy(literal),
                "_map_file": name,
            }
        )
    return data


def _resolve_context(data: dict[str, Any], runtime: RuntimeLock, stage: Path) -> ExecutionContext:
    resolved = deepcopy(data)
    for item in resolved.get("extra_big_maps", []):
        item["map_literal"] = item.pop("_map_literal")
        item.pop("_map_file", None)
    return ExecutionContext.from_dict(resolved, runtime, stage)


def _assert_execution(result: Any, action: str) -> None:
    if not result.succeeded:
        raise RuntimeError(
            f"pinned Octez rejected {action}: status={result.status}, "
            f"errors={result.error_ids}, stdout={result.raw_stdout_tail[-1200:]}, "
            f"stderr={result.raw_stderr_tail[-1200:]}"
        )


def _tzsafe_contents() -> list[dict[str, Any]]:
    template = PROJECT / "fixtures" / "growth-v2" / "templates" / "tzsafe-sign-proposal-content.json"
    contents = read_json(template)
    if not isinstance(contents, list) or len(contents) != 1:
        raise ValueError("pinned TzSafe proposal-content template must contain exactly one action")
    transfer = contents[0]
    try:
        transfer["args"][0]["args"][1] = {"int": "1"}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("pinned TzSafe proposal-content template has an unexpected Micheline shape") from exc
    return contents


def _build_tzsafe(
    stage: Path,
    runtime: RuntimeLock,
    runner: OctezRunner,
    scenarios: list[dict[str, Any]],
) -> None:
    scenario_id = "tzsafe_sign_proposal"
    script_path = stage / "contracts" / "tzsafe.json"
    owners = sorted({_address_for_index(index) for index in range(257)})
    if len(owners) != 257 or len(set(owners)) != 257:
        raise ValueError("deterministic TzSafe mockup owner set is not unique")
    owners_micheline = [_address(owner) for owner in owners]
    proposal_id = 1
    proposal_map_id, archive_map_id, metadata_map_id = 1_000_001, 1_000_002, 1_000_003
    map_data = {
        str(proposal_map_id): [],
        str(archive_map_id): [],
        str(metadata_map_id): [],
    }
    bigmaps = [
        (proposal_map_id, 0, "tzsafe-proposals-empty.json", map_data[str(proposal_map_id)]),
        (archive_map_id, 1, "tzsafe-archives-empty.json", map_data[str(archive_map_id)]),
        (metadata_map_id, 2, "tzsafe-metadata-empty.json", map_data[str(metadata_map_id)]),
    ]
    create_context_data = _context_data(
        runtime, owners[0], "create_proposal", bigmaps, now="86400", level=1
    )
    create_context = _resolve_context(create_context_data, runtime, stage)
    storage: Any = _pair(
        _nat(0),
        {"int": str(proposal_map_id)},
        {"int": str(archive_map_id)},
        owners_micheline,
        _nat(1),
        {"int": "172800"},
        {"int": str(metadata_map_id)},
    )
    contents = _tzsafe_contents()
    created = runner.run_code(script_path, storage, contents, create_context, runtime.hard_gas_limit_per_operation)
    _assert_execution(created, "TzSafe create_proposal")
    if created.operations:
        raise ValueError(
            "TzSafe create_proposal returned operations which are not applied in run_code sequence setup: "
            + canonical_json(created.operations)
        )
    if len(created.events) != 1 or created.events[0].get("tag") != "create_proposal":
        raise ValueError("TzSafe create_proposal did not emit exactly one tagged create_proposal event")
    _apply_lazy_diff(map_data, created.lazy_storage_diff)
    storage = created.storage
    proposal_key = _nat(proposal_id)
    proposal = _map_value(map_data[str(proposal_map_id)], proposal_key)
    if not isinstance(proposal, dict) or proposal.get("prim") != "Pair":
        raise ValueError("TzSafe create_proposal did not materialize proposal id 1")
    if len(proposal.get("args", [])) != 5 or proposal["args"][1] != []:
        raise ValueError("TzSafe initial proposal has an unexpected state or signature map")

    sign_parameter = _pair(
        _pair({"prim": "True"}, contents),
        _nat(proposal_id),
    )
    actions = [
        {
            "action": "create_proposal",
            "source": owners[0],
            "input_sha256": sha256_json(contents),
            "storage_sha256": sha256_json(storage),
            "lazy_storage_diff_sha256": sha256_json(created.lazy_storage_diff),
            "events_sha256": sha256_json(created.events),
        }
    ]
    snapshots: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    sequence_points = set(SNAPSHOT_SIZES)
    for signature_count in range(0, max(SNAPSHOT_SIZES) + 1):
        if signature_count in sequence_points:
            snapshot_maps = deepcopy(map_data)
            snapshot_context_data = _context_data(
                runtime,
                owners[0],
                "sign_proposal",
                [
                    (proposal_map_id, 0, f"tzsafe-proposals-{signature_count}.json", snapshot_maps[str(proposal_map_id)]),
                    (archive_map_id, 1, "tzsafe-archives-empty.json", snapshot_maps[str(archive_map_id)]),
                    (metadata_map_id, 2, "tzsafe-metadata-empty.json", snapshot_maps[str(metadata_map_id)]),
                ],
                now="86401",
                level=2,
            )
            case = _write_case_files(
                stage,
                scenario_id,
                signature_count,
                storage,
                sign_parameter,
                snapshot_context_data,
                runtime,
                sequence={"trace_file": "state-traces/tzsafe-sign-sequence.json", "index": len(snapshots)},
            )
            resolved_context = _resolve_context(snapshot_context_data, runtime, stage)
            snapshot = {
                "size": signature_count,
                "storage_sha256": sha256_json(storage),
                "input_sha256": sha256_json(sign_parameter),
                "context_sha256": sha256_json(resolved_context.to_dict()),
                "proposal_signature_count": len(proposal["args"][1]),
            }
            if snapshot["proposal_signature_count"] != signature_count:
                raise ValueError("TzSafe proposal signature state does not match its snapshot size")
            snapshots.append(snapshot)
            case_rows.append(case)
        if signature_count == max(SNAPSHOT_SIZES):
            break
        signer_index = signature_count + 1
        signer = owners[signer_index]
        step_context_data = _context_data(
            runtime,
            signer,
            "sign_proposal",
            [
                (proposal_map_id, 0, f"tzsafe-step-proposals-{signature_count + 1}.json", map_data[str(proposal_map_id)]),
                (archive_map_id, 1, "tzsafe-archives-empty.json", map_data[str(archive_map_id)]),
                (metadata_map_id, 2, "tzsafe-metadata-empty.json", map_data[str(metadata_map_id)]),
            ],
            now="86401",
            level=2,
        )
        step_context = _resolve_context(step_context_data, runtime, stage)
        before_hash = sha256_json(storage)
        result = runner.run_code(
            script_path,
            storage,
            sign_parameter,
            step_context,
            runtime.hard_gas_limit_per_operation,
        )
        _assert_execution(result, f"TzSafe sign_proposal signer #{signer_index}")
        if result.operations:
            raise ValueError("TzSafe sign_proposal emitted internal operations during state setup")
        if len(result.events) != 1 or result.events[0].get("tag") != "sign_proposal":
            raise ValueError(f"TzSafe signer #{signer_index} did not emit exactly one sign_proposal event")
        _apply_lazy_diff(map_data, result.lazy_storage_diff)
        storage = result.storage
        proposal = _map_value(map_data[str(proposal_map_id)], proposal_key)
        if not isinstance(proposal, dict) or len(proposal.get("args", [])) != 5:
            raise ValueError(f"TzSafe signer #{signer_index} did not update proposal state")
        signatures = proposal["args"][1]
        if len(signatures) != signer_index:
            raise ValueError(f"TzSafe signer #{signer_index} produced {len(signatures)} signatures")
        actions.append(
            {
                "action": "sign_proposal",
                "signer_index": signer_index,
                "source": signer,
                "input_sha256": sha256_json(sign_parameter),
                "storage_before_sha256": before_hash,
                "storage_after_sha256": sha256_json(storage),
                "lazy_storage_diff_sha256": sha256_json(result.lazy_storage_diff),
                "events_sha256": sha256_json(result.events),
            }
        )
        if signer_index % 32 == 0 or signer_index == max(SNAPSHOT_SIZES):
            print(f"TzSafe sequence: applied {signer_index}/{max(SNAPSHOT_SIZES)} signatures", flush=True)

    trace = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "state_transition_method": "sequential pinned-Octez mockup run_code calls; internal operations are not applied",
        "octez_digest": runtime.octez_digest,
        "octez_version": runtime.octez_version,
        "protocol": runtime.protocol,
        "chain_id": runtime.chain_id,
        "script_sha256": sha256_bytes(script_path.read_bytes()),
        "proposal_id": proposal_id,
        "proposal_content_sha256": sha256_json(contents),
        "actions": actions,
        "snapshots": snapshots,
    }
    trace_dir = stage / "state-traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    write_json(trace_dir / "tzsafe-sign-sequence.json", trace)
    scenarios.append(
        {
            "id": scenario_id,
            "script": "contracts/tzsafe.json",
            "entrypoint": "sign_proposal",
            "sizes": list(SNAPSHOT_SIZES),
            "storage_generator": "explicit",
            "input_generator": "explicit",
            "expected": "success",
            "cases": case_rows,
        }
    )


def _fa2_storage(ledger_id: int, operator_id: int, token_metadata_id: int, metadata_id: int) -> Any:
    return _pair(
        {"int": str(ledger_id)},
        {"int": str(operator_id)},
        {"int": str(token_metadata_id)},
        {"int": str(metadata_id)},
    )


def _fa2_context_data(
    runtime: RuntimeLock,
    ledger: list[dict[str, Any]],
    scenario_id: str,
    size: int,
) -> dict[str, Any]:
    return _context_data(
        runtime,
        DEFAULT_ADDRESS,
        "transfer",
        [
            (2_000_001, 0, f"{scenario_id}-{size}-ledger.json", ledger),
            (2_000_002, 1, "fa2-operators-empty.json", []),
            (
                2_000_003,
                2,
                "fa2-token-metadata.json",
                [_elt(_nat(0), _pair(_nat(0), []))],
            ),
            (2_000_004, 3, "fa2-metadata-empty.json", []),
        ],
        now="86400",
        level=1,
    )


def _ledger_balances(entries: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for entry in entries:
        key, value = entry["args"]
        result[key["string"]] = int(value["int"])
    return result


def _fa2_input(batch_size: int) -> Any:
    txs = [
        _pair(_address(RECIPIENT), _nat(0), _nat(1))
        for _ in range(batch_size)
    ]
    return [_pair(_address(DEFAULT_ADDRESS), txs)]


def _build_fa2(
    stage: Path,
    runtime: RuntimeLock,
    runner: OctezRunner,
    scenarios: list[dict[str, Any]],
) -> None:
    scenario_specs = (
        ("fa2_transfer_batch", False),
        ("fa2_unrelated_ledger_negative_control", True),
    )
    script = stage / "contracts" / "fa2.json"
    for scenario_id, unrelated_control in scenario_specs:
        rows: list[dict[str, Any]] = []
        for size in SIZES:
            unrelated = [
                _elt(_address(_address_for_index(index)), _nat(0))
                for index in range(1, size + 1)
            ] if unrelated_control else []
            ledger = [
                _elt(_address(DEFAULT_ADDRESS), _nat(65_536)),
                _elt(_address(RECIPIENT), _nat(0)),
                *unrelated,
            ]
            ledger.sort(key=lambda entry: entry["args"][0]["string"])
            context_data = _fa2_context_data(runtime, ledger, scenario_id, size)
            context = _resolve_context(context_data, runtime, stage)
            storage = _fa2_storage(2_000_001, 2_000_002, 2_000_003, 2_000_004)
            input_value = _fa2_input(1 if unrelated_control else size)
            case = _write_case_files(
                stage, scenario_id, size, storage, input_value, context_data, runtime
            )
            # Probe each isolated state once at the protocol ceiling. This pins whether
            # the expected result is success or a structured protocol gas exhaustion.
            result = runner.run_code(
                script,
                storage,
                input_value,
                context,
                runtime.hard_gas_limit_per_operation,
            )
            if result.succeeded:
                if result.operations:
                    raise ValueError(f"{scenario_id}:{size} emitted unexpected operations")
                updated_maps = {
                    str(item["id"]): deepcopy(item["map_literal"])
                    for item in context.extra_big_maps
                }
                _apply_lazy_diff(updated_maps, result.lazy_storage_diff)
                balances = _ledger_balances(updated_maps["2000001"])
                transferred = 1 if unrelated_control else size
                expected = {
                    DEFAULT_ADDRESS: 65_536 - transferred,
                    RECIPIENT: transferred,
                    **({
                        _address_for_index(index): 0
                        for index in range(1, size + 1)
                    } if unrelated_control else {}),
                }
                for address, amount in expected.items():
                    if balances.get(address) != amount:
                        raise ValueError(
                            f"{scenario_id}:{size} semantic balance mismatch for {address}: "
                            f"{balances.get(address)} != {amount}"
                        )
                case["expected_status"] = "success"
            elif result.status == "gas_exhausted":
                case["expected_status"] = "protocol_limit_exceeded"
            else:
                raise RuntimeError(
                    f"{scenario_id}:{size} failed for a non-gas reason: "
                    f"{result.status} {result.error_ids} {result.raw_stderr_tail[-1200:]}"
                )
            rows.append(case)
            print(
                f"fixture probe {scenario_id} size={size}: {case['expected_status']}",
                flush=True,
            )
        scenarios.append(
            {
                "id": scenario_id,
                "script": "contracts/fa2.json",
                "entrypoint": "transfer",
                "sizes": list(SIZES),
                "storage_generator": "explicit",
                "input_generator": "explicit",
                "expected": "success",
                "cases": rows,
            }
        )


def _build_synthetic(stage: Path, runtime: RuntimeLock, scenarios: list[dict[str, Any]]) -> None:
    spec = (
        ("unbounded_list_append", "unbounded_list_append.json", "list_nat", None, None, "contracts/synthetic/unbounded_list_append.json"),
        ("bounded_last64_append", "bounded_last64_append.json", "descending_list_nat", None, None, "contracts/synthetic/bounded_last64_append.json"),
        ("ordinary_map_lookup", "ordinary_map_lookup.json", "lookup_pair_map_address_nat", None, None, "contracts/synthetic/ordinary_map_lookup.json"),
        ("lazy_big_map_lookup", "lazy_big_map_lookup.json", "lookup_pair_big_map_address_nat", "address_nat", 0, "contracts/synthetic/lazy_big_map_lookup.json"),
        ("nested_allowance_lookup", "nested_allowance_lookup.json", "lookup_nested_address_allowances", None, None, "contracts/synthetic/nested_allowance_lookup.json"),
        ("flattened_allowance_big_map_lookup", "flattened_allowance_lookup.json", "lookup_pair_big_map_pair_address_address_nat", "pair_address_address_nat", 0, "contracts/synthetic/flattened_allowance_lookup.json"),
    )
    for scenario_id, script_name, storage_generator, map_name, type_index, script_ref in spec:
        rows: list[dict[str, Any]] = []
        for size in SIZES:
            storage = generate_storage(storage_generator, size)
            input_value = generate_input("unit", size)
            bigmaps: list[tuple[int, int, str, list[dict[str, Any]]]] = []
            if map_name is not None:
                map_id = 3_000_001
                literal = generate_big_map_literal(map_name, size)
                bigmaps.append((map_id, int(type_index), f"{scenario_id}-{size}.json", literal))
                storage = _pair({"int": str(map_id)}, _nat(0))
            context_data = _context_data(
                runtime, DEFAULT_ADDRESS, None, bigmaps, type_script=script_ref
            )
            case = _write_case_files(
                stage,
                scenario_id,
                size,
                storage,
                input_value,
                context_data,
                runtime,
            )
            rows.append(case)
        scenarios.append(
            {
                "id": scenario_id,
                "script": script_ref,
                "sizes": list(SIZES),
                "storage_generator": storage_generator,
                "input_generator": "unit",
                "expected": "success",
                "cases": rows,
            }
        )


def _build(stage: Path, runtime: RuntimeLock, timeout: int) -> None:
    source_refs = _copy_inputs(stage)
    scenarios: list[dict[str, Any]] = []
    _build_synthetic(stage, runtime, scenarios)
    with OctezRunner(runtime, PROJECT, timeout=timeout) as runner:
        _build_fa2(stage, runtime, runner, scenarios)
        _build_tzsafe(stage, runtime, runner, scenarios)

    manifest = {
        "schema_version": 2,
        "name": "protocol-pinned-contract-growth-corpus-v2",
        "description": (
            "Explicit growth fixtures for synthetic controls and two pinned real Tezos contracts. "
            "A case marked protocol_limit_exceeded is an observed Octez gas exhaustion, not a user incident."
        ),
        "runtime": {
            key: runtime.to_dict()[key]
            for key in (
                "octez_digest", "octez_version", "protocol", "chain_id",
                "hard_gas_limit_per_operation",
            )
        },
        "repetitions": 2,
        "scenarios": scenarios,
    }
    write_json(stage / "manifest.json", manifest)
    write_json(
        stage / "sources.json",
        {
            "schema_version": 1,
            "scope": "technical reproduction only; not adoption, demand, payment intent, grant acceptance, or revenue",
            "upstream_contracts": source_refs,
            "derived_fixture_templates": {
                "tzsafe-sign-proposal-content": {
                    "file": "templates/tzsafe-sign-proposal-content.json",
                    "sha256": sha256_bytes(
                        (stage / "templates" / "tzsafe-sign-proposal-content.json").read_bytes()
                    ),
                    "nature": (
                        "minimal representative FA2 transfer proposal content derived from the pinned "
                        "TzSafe sign_proposal integration parameter; builder sets transfer amount to one mutez"
                    ),
                    "upstream_contract": "tzsafe",
                    "upstream_operation": "sign_proposal",
                }
            },
            "synthetic_sources": {
                scenario["id"]: {
                    "source_file": scenario["script"],
                    "compiled_michelson_sha256": sha256_bytes((stage / scenario["script"]).read_bytes()),
                    "nature": "repository-owned synthetic Michelson control; not an upstream production contract",
                }
                for scenario in scenarios
                if scenario["id"] not in {"tzsafe_sign_proposal", "fa2_transfer_batch", "fa2_unrelated_ledger_negative_control"}
            },
        },
    )
    (stage / "README.md").write_text(
        "# Explicit growth corpus v2\n\n"
        "This corpus has 61 explicit cases across six synthetic controls, one TzSafe entrypoint, "
        "one FA2 transfer-batch growth path, and one FA2 unrelated-ledger negative control. "
        "The TzSafe snapshots are produced by sequential calls to the pinned Octez mockup `run_code`; "
        "internal operations are not applied. The FA2 cases begin from independent storage/context states.\n\n"
        "`size` is the declared collection cardinality or prior signer count described by the scenario; "
        "it is not a user count or a production-state claim. `expected_status: protocol_limit_exceeded` "
        "means the pinned Octez mockup returned a structured gas-exhaustion result at the protocol ceiling. "
        "It does not by itself establish a deployed incident, affected user, demand, willingness to pay, "
        "grant acceptance, or revenue. `constructed_state` means a fixture state; the TzSafe sequence "
        "label is limited to the recorded `run_code` transition chain.\n\n"
        "The minimal TzSafe proposal content is copied to `templates/tzsafe-sign-proposal-content.json`; "
        "its source relationship and SHA-256 are recorded in `sources.json`.\n\n"
        "Upstream source commits, compiler provenance, compiled Michelson hashes and MIT notices are in `sources.json` and `contracts/`.\n",
        encoding="utf-8",
    )
    _validate_cases(stage, runtime, scenarios)


def _validate_cases(stage: Path, runtime: RuntimeLock, scenarios: list[dict[str, Any]]) -> None:
    manifest, specs, _ = load_manifest(stage / "manifest.json", runtime)
    if len(specs) != 9 or sum(len(item.cases) for item in specs) != 61:
        raise ValueError("growth-v2 corpus must contain exactly 9 scenarios and 61 explicit cases")
    by_id = {item.scenario_id: item for item in specs}
    tzsafe = by_id["tzsafe_sign_proposal"]
    if tuple(item.size for item in tzsafe.cases) != SNAPSHOT_SIZES:
        raise ValueError("TzSafe signature sequence snapshot sizes are incomplete")
    for case in tzsafe.cases:
        if case.state_validity != "single_script_sequence_validated":
            raise ValueError(f"TzSafe case {case.size} is not marked sequence-validated")
    if manifest["repetitions"] != 2:
        raise ValueError("growth-v2 manifest must require exactly two measurement passes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pinned explicit Tezos growth-v2 corpus")
    parser.add_argument("--runtime", type=Path, default=PROJECT / "runtime.lock.json")
    parser.add_argument("--output", type=Path, default=TARGET)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to replace existing corpus directory: {target}")
    if args.timeout < 1:
        raise ValueError("timeout must be positive")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".growth-v2-build-", dir=target.parent))
    try:
        runtime = load_runtime(args.runtime.resolve())
        _build(stage, runtime, args.timeout)
        os.replace(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(json.dumps({"corpus": str(target), "scenarios": 9, "cases": 61}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
