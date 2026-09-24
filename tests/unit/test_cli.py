import tempfile
import unittest
from pathlib import Path

from tlsci.cli import _load_policy


class CliConfigurationTests(unittest.TestCase):
    def test_missing_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-policy.json"
            with self.assertRaises(FileNotFoundError):
                _load_policy(missing)


if __name__ == "__main__":
    unittest.main()
