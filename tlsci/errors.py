from __future__ import annotations

import json
from typing import Any


GAS_ERROR_MARKERS = (
    "gas_exhausted",
    "operation_quota_exceeded",
    "quota_exceeded",
)


def extract_json_values(text: str) -> list[Any]:
    values: list[Any] = []
    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(text):
        line_end = text.find("\n", cursor)
        if line_end < 0:
            line_end = len(text)
        search_from = cursor
        attempts = 0
        while attempts < 4:
            object_start = text.find("{", search_from, line_end)
            array_start = text.find("[", search_from, line_end)
            starts = [position for position in (object_start, array_start) if position >= 0]
            if not starts:
                cursor = line_end + 1
                break
            start = min(starts)
            try:
                value, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                # Bound malformed/logging prefixes to four parse attempts per line.
                # A valid multiline JSON value still decodes from its opening token.
                search_from = start + 1
                attempts += 1
                continue
            values.append(value)
            cursor = end
            break
        else:
            cursor = line_end + 1
            continue
    return values


def collect_error_ids(stdout: str, stderr: str) -> tuple[str, ...]:
    ids: list[str] = []
    pending = extract_json_values(f"{stdout}\n{stderr}")
    while pending:
        candidate = pending.pop()
        if isinstance(candidate, dict):
            error_id = candidate.get("id")
            if isinstance(error_id, str) and error_id not in ids:
                ids.append(error_id)
            pending.extend(candidate.values())
        elif isinstance(candidate, list):
            pending.extend(candidate)
    return tuple(ids)


def classify_error(stdout: str, stderr: str, return_code: int) -> tuple[str, tuple[str, ...]]:
    error_ids = collect_error_ids(stdout, stderr)
    haystack = " ".join(error_ids).lower() + " " + stdout.lower() + " " + stderr.lower()
    if return_code == 124 or "timed out" in haystack:
        return "timeout", error_ids
    # Only structured Tezos error IDs prove gas exhaustion. A contract may
    # legitimately FAILWITH with text containing words such as "gas limit".
    if any(
        marker in error_id.lower()
        for error_id in error_ids
        for marker in GAS_ERROR_MARKERS
    ):
        return "gas_exhausted", error_ids
    if "script_rejected" in haystack or "failwith" in haystack:
        return "script_rejected", error_ids
    if "type_error" in haystack or "ill_typed" in haystack or "invalid_contract" in haystack:
        return "type_error", error_ids
    return "rpc_error", error_ids
