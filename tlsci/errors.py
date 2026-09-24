from __future__ import annotations

import json
from typing import Any


GAS_ERROR_MARKERS = (
    "gas_exhausted",
    "gas_limit",
    "operation_quota_exceeded",
    "quota_exceeded",
    "gas_exhausted.operation",
)


def extract_json_values(text: str) -> list[Any]:
    values: list[Any] = []
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        values.append(value)
    return values


def collect_error_ids(stdout: str, stderr: str) -> tuple[str, ...]:
    ids: list[str] = []
    for value in extract_json_values(f"{stdout}\n{stderr}"):
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
                if candidate["id"] not in ids:
                    ids.append(candidate["id"])
    return tuple(ids)


def classify_error(stdout: str, stderr: str, return_code: int) -> tuple[str, tuple[str, ...]]:
    error_ids = collect_error_ids(stdout, stderr)
    haystack = " ".join(error_ids).lower() + " " + stdout.lower() + " " + stderr.lower()
    if any(marker in haystack for marker in GAS_ERROR_MARKERS):
        return "gas_exhausted", error_ids
    if "script_rejected" in haystack or "failwith" in haystack or "runtime_error" in haystack:
        return "script_rejected", error_ids
    if "type_error" in haystack or "ill_typed" in haystack or "invalid_contract" in haystack:
        return "type_error", error_ids
    if return_code == 124 or "timed out" in haystack:
        return "timeout", error_ids
    return "rpc_error", error_ids
