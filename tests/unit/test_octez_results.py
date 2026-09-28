import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tlsci.models import ExecutionContext, RuntimeLock
from tlsci.octez import OctezRunner


class OctezResultParsingTests(unittest.TestCase):
    def test_event_records_are_not_counted_as_internal_operations(self) -> None:
        runtime = RuntimeLock(1, "image", "digest", "version", "protocol", "chain", 1000)
        with tempfile.TemporaryDirectory() as payload_dir:
            payload_path = Path(payload_dir)
            runner = OctezRunner(runtime, Path(__file__).resolve().parents[2])
            runner.container_name = "test-container"
            runner._payload_dir = payload_path
            event = {"kind": "event", "tag": "test_event", "payload": {"int": "1"}}
            internal = {"kind": "transaction", "amount": "0"}
            response = {"storage": {"prim": "Unit"}, "operations": [event, internal]}
            with patch(
                "tlsci.octez.subprocess.run",
                return_value=type(
                    "Completed",
                    (),
                    {"returncode": 0, "stdout": json.dumps(response), "stderr": ""},
                )(),
            ):
                result = runner.run_code(
                    Path("fixtures/synthetic/list_append.json"),
                    {"prim": "Nil", "args": [{"prim": "nat"}]},
                    {"prim": "Unit"},
                    ExecutionContext(chain_id="chain"),
                    100,
                )
            self.assertTrue(result.succeeded)
            self.assertEqual(result.operations, [internal])
            self.assertEqual(result.events, [event])


if __name__ == "__main__":
    unittest.main()
