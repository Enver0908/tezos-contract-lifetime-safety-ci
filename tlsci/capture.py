from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .util import utc_now, write_json


class CaptureError(RuntimeError):
    pass


def _get_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "tezos-lifetime-safety-ci/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CaptureError(f"GET failed: {url}: {exc}") from exc


def capture_contract(address: str, output_dir: Path, rpc: str = "https://rpc.tzkt.io/mainnet", block: str = "head") -> dict[str, Any]:
    block_hash = _get_json(f"{rpc}/chains/main/blocks/{block}/hash")
    header = _get_json(f"{rpc}/chains/main/blocks/{block_hash}")
    script = _get_json(f"{rpc}/chains/main/blocks/{block_hash}/context/contracts/{address}/script")
    storage = _get_json(f"{rpc}/chains/main/blocks/{block_hash}/context/contracts/{address}/storage")
    entrypoints = _get_json(f"{rpc}/chains/main/blocks/{block_hash}/context/contracts/{address}/entrypoints")
    target = output_dir / address
    target.mkdir(parents=True, exist_ok=True)
    if isinstance(script, dict) and isinstance(script.get("code"), list):
        code = script["code"]
    elif isinstance(script, list):
        code = script
    else:
        raise CaptureError(f"contract script response does not contain Micheline code: {address}")
    write_json(target / "script.json", code)
    write_json(target / "script.response.json", script)
    write_json(target / "storage.snapshot.json", storage)
    write_json(target / "entrypoints.json", entrypoints)
    metadata = {
        "address": address,
        "rpc": rpc,
        "requested_block": block,
        "block_hash": block_hash,
        "block_level": header.get("header", {}).get("level"),
        "protocol": header.get("protocol"),
        "script_sha256": __import__("hashlib").sha256(json.dumps(code, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "captured_at_utc": utc_now(),
        "source": "mainnet_read_only_rpc",
    }
    write_json(target / "provenance.json", metadata)
    return metadata


def capture_big_map_key(
    ptr: int,
    key: str,
    output_file: Path,
    api: str = "https://api.tzkt.io",
) -> dict[str, Any]:
    url = f"{api.rstrip('/')}/v1/bigmaps/{ptr}/keys/{quote(key, safe='')}"
    entry = _get_json(url)
    if not isinstance(entry, dict) or "value" not in entry:
        raise CaptureError(f"big-map key response is not an entry: {url}")
    value = entry["value"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"big-map value is not Micheline JSON: {url}") from exc
    map_key: dict[str, str]
    if key.lstrip("-").isdigit():
        map_key = {"int": key}
    else:
        map_key = {"string": key}
    payload = {
        "ptr": str(ptr),
        "source_url": url,
        "entry": entry,
        "map_literal": [{"prim": "Elt", "args": [map_key, value]}],
    }
    write_json(output_file, payload)
    return {
        "ptr": ptr,
        "key": key,
        "output": str(output_file),
        "source_url": url,
        "active": entry.get("active"),
    }
