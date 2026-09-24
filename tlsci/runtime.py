from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import extract_json_values
from .models import RuntimeLock
from .util import read_json


class RuntimeErrorState(RuntimeError):
    pass


def load_runtime(path: Path) -> RuntimeLock:
    return RuntimeLock.from_dict(read_json(path))


def docker_octez_command(runtime: RuntimeLock, *args: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "octez-client",
        runtime.octez_digest,
        "--protocol",
        runtime.protocol,
        "--mode",
        "mockup",
        *args,
    ]


def run_runtime_command(runtime: RuntimeLock, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    command = docker_octez_command(runtime, *args)
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeErrorState("docker executable was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeErrorState(f"Octez command timed out after {timeout}s") from exc


def doctor(runtime: RuntimeLock) -> dict[str, Any]:
    from .octez import OctezRunner

    try:
        with tempfile.TemporaryDirectory(prefix="tlsci-doctor-") as workdir:
            with OctezRunner(runtime, Path(workdir), timeout=30) as runner:
                version = runner.run_client_command("--version")
                protocols = runner.run_client_command("list", "mockup", "protocols")
                constants = runner.run_client_command(
                    "rpc",
                    "get",
                    "/chains/main/blocks/head/context/constants",
                )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise RuntimeErrorState(f"Octez doctor could not complete: {exc}") from exc
    version_text = (version.stdout or version.stderr).strip()
    protocol_text = protocols.stdout.strip()
    constants_values = extract_json_values(f"{constants.stdout}\n{constants.stderr}")
    constants_data = next(
        (
            value
            for value in reversed(constants_values)
            if isinstance(value, dict) and "hard_gas_limit_per_operation" in value
        ),
        None,
    )
    if version.returncode != 0:
        raise RuntimeErrorState(f"Octez image failed --version: {version_text[-1000:]}")
    if protocols.returncode != 0 or runtime.protocol not in protocol_text:
        raise RuntimeErrorState(
            f"pinned protocol {runtime.protocol} is unavailable in mockup: {protocol_text[-1000:]}"
        )
    if constants.returncode != 0 or not isinstance(constants_data, dict):
        raise RuntimeErrorState(
            "mockup protocol constants could not be read: "
            f"{(constants.stderr or constants.stdout)[-1000:]}"
        )
    try:
        effective_hard_gas = int(constants_data["hard_gas_limit_per_operation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeErrorState(
            "mockup protocol constants contain an invalid hard_gas_limit_per_operation"
        ) from exc
    if effective_hard_gas != runtime.hard_gas_limit_per_operation:
        raise RuntimeErrorState(
            "runtime lock hard gas limit does not match mockup protocol constants: "
            f"lock={runtime.hard_gas_limit_per_operation}, mockup={effective_hard_gas}"
        )
    return {
        "octez_version_output": version_text,
        "protocols_output": protocol_text,
        "image": runtime.octez_digest,
        "protocol": runtime.protocol,
        "hard_gas_limit_per_operation": effective_hard_gas,
        "constants_source": "/chains/main/blocks/head/context/constants",
    }
