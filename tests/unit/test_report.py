import json
import tempfile
import unittest
from pathlib import Path

from tlsci.models import Measurement, RunReport
from tlsci.report import save_report


class ReportSafetyTests(unittest.TestCase):
    def test_report_writer_refuses_to_overwrite_any_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = RunReport(
                schema_version=3,
                generated_at_utc="2026-09-27T00:00:00Z",
                runtime={"protocol": "test"},
                manifest_sha256="manifest",
                measurements=[
                    Measurement(
                        scenario_id="case",
                        size=0,
                        status="success",
                        minimum_successful_gas_budget=1,
                        last_failed_gas_budget=None,
                        first_successful_gas_budget=1,
                        gas_cap=100,
                        storage_bytes=2,
                        operations_count=0,
                        fixture_sha256="fixture",
                        code_sha256="code",
                        measurement_method="integer_budget_search_v3",
                        semantic_output={"storage": {"prim": "Unit"}},
                    )
                ],
            )
            paths = (root / "report.json", root / "report.md", root / "report.html")
            save_report(report, paths[0], paths[1], 0, paths[2])
            original = paths[0].read_bytes()
            with self.assertRaises(FileExistsError):
                save_report(report, paths[0], paths[1], 0, paths[2])
            self.assertEqual(paths[0].read_bytes(), original)
            data = json.loads(original)
            self.assertEqual(data["measurements"][0]["semantic_output"]["storage"]["prim"], "Unit")


if __name__ == "__main__":
    unittest.main()
