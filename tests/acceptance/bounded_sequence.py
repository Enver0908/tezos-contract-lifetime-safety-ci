import unittest
from pathlib import Path

from tlsci.growth import generate_storage
from tlsci.models import ExecutionContext
from tlsci.octez import OctezRunner
from tlsci.runtime import load_runtime


class BoundedSequenceAcceptanceTests(unittest.TestCase):
    def test_boundary_and_repeated_calls_stop_at_64_items(self) -> None:
        project = Path(__file__).resolve().parents[2]
        runtime = load_runtime(project / "runtime.lock.json")
        fixtures = project / "fixtures" / "synthetic"
        script = fixtures / "bounded_list.json"
        context = ExecutionContext.from_dict({}, runtime, fixtures)
        input_value = {"prim": "Unit"}

        with OctezRunner(runtime, fixtures, timeout=60) as runner:
            for size, expected_length in ((63, 64), (64, 64), (65, 65)):
                with self.subTest(size=size):
                    boundary = runner.run_code(
                        script,
                        generate_storage("list_nat", size),
                        input_value,
                        context,
                        runtime.hard_gas_limit_per_operation,
                    )
                    self.assertTrue(boundary.succeeded, boundary.raw_stderr_tail)
                    self.assertIsInstance(boundary.storage, list)
                    self.assertEqual(len(boundary.storage), expected_length)

            first = runner.run_code(
                script,
                generate_storage("list_nat", 63),
                input_value,
                context,
                runtime.hard_gas_limit_per_operation,
            )
            self.assertTrue(first.succeeded, first.raw_stderr_tail)
            self.assertIsInstance(first.storage, list)
            self.assertEqual(len(first.storage), 64)

            second = runner.run_code(
                script,
                first.storage,
                input_value,
                context,
                runtime.hard_gas_limit_per_operation,
            )
            self.assertTrue(second.succeeded, second.raw_stderr_tail)
            self.assertIsInstance(second.storage, list)
            self.assertEqual(len(second.storage), 64)
