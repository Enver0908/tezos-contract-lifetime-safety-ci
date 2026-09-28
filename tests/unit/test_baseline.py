import json
import tempfile
import unittest
from pathlib import Path

from tlsci.baseline import load_baseline


class BaselineValidationTests(unittest.TestCase):
    def _write(self, data):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "baseline.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_schema_v1_requires_fresh_measurement(self) -> None:
        path = self._write({"schema_version": 1, "runtime": {}, "scenarios": {"x:0": {}}})
        with self.assertRaisesRegex(ValueError, "regenerate"):
            load_baseline(path)

    def test_non_object_runtime_fails_closed(self) -> None:
        path = self._write({"schema_version": 2, "runtime": [], "scenarios": {"x:0": {}}})
        with self.assertRaisesRegex(ValueError, "JSON objects"):
            load_baseline(path)

    def test_schema_v2_rejects_missing_cap_kind(self) -> None:
        path = self._write({
            "schema_version": 2,
            "runtime": {},
            "scenarios": {
                "x:0": {
                    "minimum_successful_gas_budget": 100,
                    "gas_cap": 1000,
                    "fixture_sha256": "fixture",
                    "code_sha256": "code",
                    "measurement_method": "integer_budget_search_v2",
                }
            },
        })
        with self.assertRaisesRegex(ValueError, "cap_kind"):
            load_baseline(path)

    def test_schema_v2_accepts_complete_entry(self) -> None:
        path = self._write({
            "schema_version": 2,
            "runtime": {"protocol": "protocol"},
            "scenarios": {
                "x:0": {
                    "minimum_successful_gas_budget": 100,
                    "gas_cap": 1000,
                    "cap_kind": "protocol",
                    "fixture_sha256": "fixture",
                    "code_sha256": "code",
                    "measurement_method": "integer_budget_search_v2",
                }
            },
        })
        self.assertEqual(load_baseline(path).schema_version, 2)

    def test_schema_v3_accepts_explicit_case_measurement(self) -> None:
        path = self._write({
            "schema_version": 3,
            "runtime": {"protocol": "protocol"},
            "scenarios": {
                "x:0": {
                    "minimum_successful_gas_budget": 100,
                    "gas_cap": 1000,
                    "cap_kind": "protocol",
                    "fixture_sha256": "fixture",
                    "code_sha256": "code",
                    "measurement_method": "integer_budget_search_v3",
                }
            },
        })
        self.assertEqual(load_baseline(path).schema_version, 3)
