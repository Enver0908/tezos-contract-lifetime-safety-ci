import json
import tempfile
import unittest
from pathlib import Path

from tlsci.models import RuntimeLock
from tlsci.validation import ManifestError, load_manifest


RUNTIME = RuntimeLock(
    schema_version=1,
    octez_image="tezos/tezos:octez-v25.2",
    octez_digest="tezos/tezos@sha256:test",
    octez_version="Octez 25.2",
    protocol="protocol",
    chain_id="chain",
    hard_gas_limit_per_operation=1_000,
)

SCRIPT = [
    {"prim": "parameter", "args": [{"prim": "unit"}]},
    {"prim": "storage", "args": [{"prim": "unit"}]},
    {"prim": "code", "args": [[]]},
]


class ManifestValidationTests(unittest.TestCase):
    def _write_manifest(self, directory: Path, **scenario_overrides: object) -> Path:
        (directory / "script.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
        scenario = {
            "id": "scenario",
            "script": "script.json",
            "sizes": [0],
            "storage_generator": "unit",
            "input_generator": "unit",
            "expected": "success",
            **scenario_overrides,
        }
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")
        return manifest

    def test_script_shape_is_checked_during_manifest_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            manifest = self._write_manifest(path)
            _, scenarios, _ = load_manifest(manifest, RUNTIME)
            self.assertEqual(scenarios[0].scenario_id, "scenario")

    def test_unsupported_expected_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._write_manifest(Path(directory), expected="reject")
            with self.assertRaises(ManifestError):
                load_manifest(manifest, RUNTIME)

    def test_gas_cap_above_runtime_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._write_manifest(Path(directory), gas_cap=1001)
            with self.assertRaises(ManifestError):
                load_manifest(manifest, RUNTIME)

    def test_template_generator_without_template_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._write_manifest(Path(directory), storage_generator="template")
            with self.assertRaises(ManifestError):
                load_manifest(manifest, RUNTIME)

    def test_gas_sensitive_instruction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            manifest = self._write_manifest(path)
            script = json.loads((path / "script.json").read_text(encoding="utf-8"))
            script[2]["args"] = [[{"prim": "STEPS_TO_QUOTA"}]]
            (path / "script.json").write_text(json.dumps(script), encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(manifest, RUNTIME)
