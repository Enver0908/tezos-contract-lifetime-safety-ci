import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tlsci.cli import PROJECT_ROOT, main
from tlsci.runtime import load_runtime
from tlsci.util import sha256_json, write_json


def _report(exit_code: int = 0, budget: int = 100) -> dict[str, object]:
    row = {
        "scenario_id": "explicit",
        "size": 1,
        "status": "success",
        "minimum_successful_gas_budget": budget,
        "last_failed_gas_budget": budget - 1,
        "first_successful_gas_budget": budget,
        "gas_cap": 1000,
        "storage_bytes": 13,
        "operations_count": 0,
        "fixture_sha256": "fixture-hash",
        "code_sha256": "code-hash",
        "measurement_method": "integer_budget_search_v3",
        "execution_scope": "single_script",
        "state_validity": "constructed_state",
        "internal_operations_executed": False,
        "error_ids": [],
        "cap_kind": "protocol",
        "context_sha256": "context-hash",
        "semantic_output_sha256": "semantic-hash",
        "semantic_output": {"storage": {"prim": "Unit"}, "operations": [], "lazy_storage_diff": None, "events": []},
        "input_bytes": 13,
        "state_trace_sha256": None,
    }
    return {
        "schema_version": 3,
        "generated_at_utc": "2026-09-27T00:00:00Z",
        "runtime": load_runtime(PROJECT_ROOT / "runtime.lock.json").to_dict(),
        "manifest_sha256": "manifest-hash",
        "measurements": [row],
        "decisions": [],
        "errors": [],
        "coverage": {
            "expected_cases": ["explicit:1"],
            "observed_cases": ["explicit:1"],
            "missing_cases": [],
            "unexpected_cases": [],
            "duplicate_cases": [],
            "complete": True,
        },
        "provenance": {
            "manifest_data": {"schema_version": 2},
            "reproducibility": {"required": True, "runs_per_scenario": 2, "passed": True, "mismatches": []},
            "measurement_set_sha256": sha256_json([row]),
        },
        "exit_code": exit_code,
    }


class SchemaV3CliTests(unittest.TestCase):
    def test_offline_policy_evaluation_does_not_need_octez(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "run-report.json"
            policy_path = root / "policy.json"
            output_path = root / "evaluated"
            write_json(report_path, _report(budget=20_001))
            policy_path.write_text(
                json.dumps({"max_successful_gas_budget": 20_000}), encoding="utf-8"
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "evaluate",
                    "--report", str(report_path),
                    "--output-dir", str(output_path),
                    "--policy", str(policy_path),
                ])
            self.assertEqual(code, 1)
            evaluated = json.loads((output_path / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(evaluated["decisions"][0]["rule_ids"], ["TEST_BUDGET_EXCEEDED"])
            self.assertTrue(evaluated["provenance"]["policy_evaluation"])

    def test_baseline_creation_requires_complete_schema_v3_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            baseline_path = root / "baseline.json"
            data = _report(budget=100)
            data["decisions"] = [{"decision": "pass"}]
            write_json(report_path, data)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([
                    "baseline", "create",
                    "--report", str(report_path),
                    "--output", str(baseline_path),
                ])
            self.assertEqual(code, 0, stderr.getvalue())
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            self.assertEqual(baseline["schema_version"], 3)
            self.assertIn("explicit:1", baseline["scenarios"])

    def test_offline_evaluation_rejects_incomplete_coverage_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = _report()
            data["coverage"]["complete"] = False
            report_path = root / "report.json"
            output_path = root / "out"
            policy_path = root / "policy.json"
            write_json(report_path, data)
            policy_path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([
                    "evaluate", "--report", str(report_path),
                    "--output-dir", str(output_path), "--policy", str(policy_path),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
