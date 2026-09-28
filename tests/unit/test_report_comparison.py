import unittest
from pathlib import Path

from tools.compare_reports import ReportComparisonError, comparable_report


def report():
    return {
        "schema_version": 2,
        "generated_at_utc": "2026-09-26T00:00:00Z",
        "exit_code": 0,
        "errors": [],
        "runtime": {"protocol": "protocol"},
        "manifest_sha256": "manifest-hash",
        "measurements": [{
            "status": "success",
            "minimum_successful_gas_budget": 100,
            "fixture_sha256": "fixture",
        }],
        "decisions": [{"decision": "pass", "rule_ids": []}],
        "provenance": {
            "manifest": "runner-specific/path/manifest.json",
            "manifest_data": {"schema_version": 1},
            "measurement_limit": "single_script",
            "gas_metric": "minimum_successful_integer_gas_budget",
            "reproducibility": {
                "required": True,
                "runs_per_scenario": 2,
                "passed": True,
                "mismatches": [],
            },
        },
    }


def explicit_report(expected_status="success"):
    measurement = {
        "scenario_id": "explicit",
        "size": 0,
        "status": "success" if expected_status == "success" else "budget_exceeded",
        "minimum_successful_gas_budget": 100 if expected_status == "success" else None,
        "gas_cap": 1000,
        "cap_kind": "protocol",
    }
    decision = {
        "scenario_id": "explicit",
        "size": 0,
        "decision": "pass" if expected_status == "success" else "violation",
        "rule_ids": [] if expected_status == "success" else ["PROTOCOL_LIMIT_EXCEEDED"],
    }
    return {
        "schema_version": 3,
        "generated_at_utc": "2026-09-27T00:00:00Z",
        "exit_code": 0 if expected_status == "success" else 1,
        "errors": [],
        "runtime": {"protocol": "protocol"},
        "manifest_sha256": "manifest-hash",
        "measurements": [measurement],
        "decisions": [decision],
        "coverage": {
            "expected_cases": ["explicit:0"],
            "observed_cases": ["explicit:0"],
            "missing_cases": [],
            "unexpected_cases": [],
            "duplicate_cases": [],
            "complete": True,
        },
        "provenance": {
            "manifest_data": {
                "schema_version": 2,
                "scenarios": [{"id": "explicit", "cases": [{"size": 0, "expected_status": expected_status}]}],
            },
            "measurement_limit": "single_script",
            "gas_metric": "minimum_successful_integer_gas_budget",
            "reproducibility": {
                "required": True,
                "runs_per_scenario": 2,
                "passed": True,
                "mismatches": [],
            },
        },
    }


class ReportComparisonTests(unittest.TestCase):
    def test_normalization_ignores_runner_path_and_timestamp(self) -> None:
        first = report()
        second = report()
        second["generated_at_utc"] = "2026-09-26T00:01:00Z"
        second["provenance"]["manifest"] = "another/runner/manifest.json"
        self.assertEqual(
            comparable_report(first, Path("first.json")),
            comparable_report(second, Path("second.json")),
        )

    def test_semantic_measurement_difference_is_preserved(self) -> None:
        first = report()
        second = report()
        second["measurements"][0]["minimum_successful_gas_budget"] = 101
        self.assertNotEqual(
            comparable_report(first, Path("first.json")),
            comparable_report(second, Path("second.json")),
        )

    def test_failed_report_is_not_comparable(self) -> None:
        data = report()
        data["exit_code"] = 2
        with self.assertRaisesRegex(ReportComparisonError, "cleanly"):
            comparable_report(data, Path("failed.json"))

    def test_schema_v3_protocol_limit_outcome_is_explicitly_comparable(self) -> None:
        normalized = comparable_report(explicit_report("protocol_limit_exceeded"), Path("growth.json"))
        self.assertEqual(normalized["exit_code"], 1)
        self.assertTrue(normalized["coverage"]["complete"])

    def test_schema_v3_legacy_manifest_report_remains_comparable(self) -> None:
        data = report()
        data["schema_version"] = 3
        data["coverage"] = {
            "expected_cases": ["legacy:0"],
            "observed_cases": ["legacy:0"],
            "missing_cases": [],
            "unexpected_cases": [],
            "duplicate_cases": [],
            "complete": True,
        }
        normalized = comparable_report(data, Path("legacy-v3.json"))
        self.assertEqual(normalized["schema_version"], 3)
        self.assertEqual(normalized["exit_code"], 0)

    def test_schema_v3_missing_case_is_not_comparable(self) -> None:
        data = explicit_report()
        data["coverage"]["complete"] = False
        with self.assertRaisesRegex(ReportComparisonError, "coverage"):
            comparable_report(data, Path("partial.json"))
