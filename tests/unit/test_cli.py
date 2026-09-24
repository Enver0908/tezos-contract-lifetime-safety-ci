import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tlsci.capture import CaptureError
from tlsci.cli import PROJECT_ROOT, _load_policy, main
from tlsci.runtime import RuntimeErrorState


class CliConfigurationTests(unittest.TestCase):
    def test_missing_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-policy.json"
            with self.assertRaises(FileNotFoundError):
                _load_policy(missing)

    def test_missing_manifest_returns_input_error_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-manifest.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "--runtime",
                        str(PROJECT_ROOT / "runtime.lock.json"),
                        "validate",
                        "--manifest",
                        str(missing),
                    ]
                )

            self.assertEqual(result, 2)
            self.assertIn("error:", stderr.getvalue())

    def test_invalid_scenario_value_returns_input_error_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scenarios": [
                            {
                                "id": "bad-size",
                                "script": "script.json",
                                "sizes": ["not-an-integer"],
                                "storage_generator": "unit",
                                "input_generator": "unit",
                                "expected": "success",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (Path(directory) / "script.json").write_text("[]", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "--runtime",
                        str(PROJECT_ROOT / "runtime.lock.json"),
                        "validate",
                        "--manifest",
                        str(manifest),
                    ]
                )

            self.assertEqual(result, 2)
            self.assertIn("error:", stderr.getvalue())

    def test_missing_policy_returns_input_error_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-policy.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "--runtime",
                        str(PROJECT_ROOT / "runtime.lock.json"),
                        "run",
                        "--manifest",
                        str(PROJECT_ROOT / "fixtures" / "synthetic" / "manifest.json"),
                        "--policy",
                        str(missing),
                        "--output-dir",
                        str(Path(directory) / "output"),
                    ]
                )

            self.assertEqual(result, 2)
            self.assertIn("error:", stderr.getvalue())

    def test_runtime_failure_returns_execution_error_exit_code(self) -> None:
        stderr = io.StringIO()
        with patch("tlsci.cli.doctor", side_effect=RuntimeErrorState("runtime unavailable")):
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "--runtime",
                        str(PROJECT_ROOT / "runtime.lock.json"),
                        "doctor",
                    ]
                )

        self.assertEqual(result, 3)
        self.assertIn("error:", stderr.getvalue())

    def test_capture_failure_returns_execution_error_exit_code(self) -> None:
        stderr = io.StringIO()
        with patch("tlsci.cli.capture_contract", side_effect=CaptureError("RPC unavailable")):
            with contextlib.redirect_stderr(stderr):
                result = main(["capture", "--address", "KT1-test"])

        self.assertEqual(result, 3)
        self.assertIn("error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
