from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import classify_error, extract_json_values
from .models import ExecutionContext, ExecutionResult, RuntimeLock
from .runtime import docker_octez_command
from .util import canonical_json, sha256_bytes, tail


class OctezRunner:
    def __init__(self, runtime: RuntimeLock, project_root: Path, timeout: int = 30) -> None:
        self.runtime = runtime
        self.project_root = project_root
        self.timeout = timeout
        self.container_name: str | None = None

    def __enter__(self) -> "OctezRunner":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def start(self) -> None:
        if self.container_name is not None:
            return
        self.container_name = f"tlsci-{os.getpid()}-{uuid.uuid4().hex[:10]}"
        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{self.project_root.resolve()}:/workspace:ro",
            "--name",
            self.container_name,
            "--entrypoint",
            "sleep",
            self.runtime.octez_digest,
            "infinity",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            self.container_name = None
            raise RuntimeError("docker executable was not found") from exc
        if completed.returncode != 0:
            name = self.container_name
            self.container_name = None
            raise RuntimeError(f"failed to start Octez container {name}: {completed.stderr[-1000:]}")

    def close(self) -> None:
        if self.container_name is None:
            return
        name = self.container_name
        self.container_name = None
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=self.timeout, check=False)

    def _octez_command(self, *args: str) -> list[str]:
        if self.container_name is None:
            return docker_octez_command(self.runtime, *args)
        return [
            "docker",
            "exec",
            self.container_name,
            "octez-client",
            "--protocol",
            self.runtime.protocol,
            "--mode",
            "mockup",
            *args,
        ]

    def _script_path(self, script: Path) -> Path:
        path = script if script.is_absolute() else self.project_root / script
        if not path.is_file():
            raise FileNotFoundError(f"script file not found: {path}")
        return path

    def run_code(
        self,
        script: Path,
        storage: Any,
        input_value: Any,
        context: ExecutionContext,
        gas_budget: int,
    ) -> ExecutionResult:
        script_path = self._script_path(script)
        code = json.loads(script_path.read_text(encoding="utf-8"))
        if isinstance(code, dict) and isinstance(code.get("code"), list):
            code = code["code"]
        payload: dict[str, Any] = {
            "script": code,
            "storage": storage,
            "input": input_value,
            "amount": context.amount,
            "balance": context.balance,
            "chain_id": context.chain_id,
            "source": context.source,
            "gas": str(gas_budget),
        }
        if context.payer is not None:
            payload["payer"] = context.payer
        if context.now is not None:
            payload["now"] = context.now
        if context.level is not None:
            payload["level"] = str(context.level)
        if context.entrypoint is not None:
            payload["entrypoint"] = context.entrypoint
        if context.other_contracts:
            payload["other_contracts"] = list(context.other_contracts)
        if context.extra_big_maps:
            payload["extra_big_maps"] = list(context.extra_big_maps)

        body = canonical_json(payload)
        payload_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix=".tlsci-payload-",
                dir=self.project_root,
                delete=False,
            ) as payload_file:
                payload_file.write(body)
                payload_path = Path(payload_file.name)
            command = self._octez_command(
                "rpc",
                "post",
                "/chains/main/blocks/head/helpers/scripts/run_code",
                "with",
                f"file:/workspace/{payload_path.name}",
            )
        except OSError as exc:
            return ExecutionResult(
                status="rpc_error",
                gas_budget=gas_budget,
                error_ids=("tlsci.payload_file",),
                raw_stderr_tail=tail(str(exc)),
            )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                status="rpc_error",
                gas_budget=gas_budget,
                error_ids=("docker.not_found",),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                status="timeout",
                gas_budget=gas_budget,
                error_ids=("tlsci.timeout",),
                raw_stdout_tail=tail(str(exc.stdout or "")),
                raw_stderr_tail=tail(str(exc.stderr or "")),
            )
        finally:
            if payload_path is not None:
                try:
                    payload_path.unlink()
                except OSError:
                    pass

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        values = extract_json_values(stdout)
        output = next(
            (
                value
                for value in reversed(values)
                if isinstance(value, dict) and "storage" in value and "operations" in value
            ),
            None,
        )
        if isinstance(output, dict) and "storage" in output and "operations" in output:
            return ExecutionResult(
                status="success",
                gas_budget=gas_budget,
                storage=output.get("storage"),
                operations=list(output.get("operations", [])),
                lazy_storage_diff=output.get("lazy_storage_diff"),
                stdout_sha256=sha256_bytes(stdout.encode("utf-8")),
                stderr_sha256=sha256_bytes(stderr.encode("utf-8")),
                raw_stdout_tail=tail(stdout),
                raw_stderr_tail=tail(stderr),
            )

        status, error_ids = classify_error(stdout, stderr, completed.returncode)
        return ExecutionResult(
            status=status,
            gas_budget=gas_budget,
            error_ids=error_ids,
            stdout_sha256=sha256_bytes(stdout.encode("utf-8")),
            stderr_sha256=sha256_bytes(stderr.encode("utf-8")),
            raw_stdout_tail=tail(stdout),
            raw_stderr_tail=tail(stderr),
        )
