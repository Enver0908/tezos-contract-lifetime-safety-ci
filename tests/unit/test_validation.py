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

    def test_unsorted_growth_sizes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._write_manifest(Path(directory), sizes=[0, 10, 5])
            with self.assertRaisesRegex(ManifestError, "ascending order"):
                load_manifest(manifest, RUNTIME)

    def test_non_object_scenario_is_rejected_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"schema_version": 1, "scenarios": [None]}), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "JSON object"):
                load_manifest(path, RUNTIME)

    def _write_v2_manifest(self, directory: Path, case_overrides: dict[str, object] | None = None) -> Path:
        (directory / "script.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
        (directory / "storage.json").write_text(json.dumps({"prim": "Unit"}), encoding="utf-8")
        (directory / "input.json").write_text(json.dumps({"prim": "Unit"}), encoding="utf-8")
        (directory / "context.json").write_text(json.dumps({"chain_id": RUNTIME.chain_id}), encoding="utf-8")
        case = {
            "size": 0,
            "storage_file": "storage.json",
            "input_file": "input.json",
            "context_file": "context.json",
            **(case_overrides or {}),
        }
        manifest = {
            "schema_version": 2,
            "runtime": {
                key: RUNTIME.to_dict()[key]
                for key in ("octez_digest", "octez_version", "protocol", "chain_id", "hard_gas_limit_per_operation")
            },
            "repetitions": 2,
            "scenarios": [
                {
                    "id": "explicit",
                    "script": "script.json",
                    "expected": "success",
                    "cases": [case],
                }
            ],
        }
        path = directory / "manifest-v2.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_explicit_manifest_accepts_case_files_and_uses_case_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_v2_manifest(Path(directory))
            _, scenarios, _ = load_manifest(path, RUNTIME)
            self.assertEqual(scenarios[0].sizes, (0,))
            self.assertEqual(scenarios[0].cases[0].expected_status, "success")

    def test_explicit_manifest_allows_omitted_optional_context_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_v2_manifest(Path(directory))
            _, scenarios, _ = load_manifest(path, RUNTIME)
            self.assertEqual(scenarios[0].cases[0].size, 0)

    def test_explicit_manifest_rejects_non_array_context_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_v2_manifest(Path(directory))
            context_path = Path(directory) / "context.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["other_contracts"] = {}
            context_path.write_text(json.dumps(context), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "other_contracts.*JSON array"):
                load_manifest(path, RUNTIME)

    def test_explicit_manifest_rejects_paths_escaping_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_v2_manifest(Path(directory), {"storage_file": "../outside.json"})
            with self.assertRaisesRegex(ManifestError, "escapes"):
                load_manifest(path, RUNTIME)

    def test_explicit_manifest_rejects_boolean_or_string_case_size(self) -> None:
        for value in (True, "0"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = self._write_v2_manifest(Path(directory), {"size": value})
                with self.assertRaisesRegex(ManifestError, "size must be an integer"):
                    load_manifest(path, RUNTIME)

    def test_explicit_manifest_requires_pinned_runtime_and_two_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_v2_manifest(Path(directory))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["runtime"]["protocol"] = "other"
            data["repetitions"] = 1
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "runtime does not match"):
                load_manifest(path, RUNTIME)
