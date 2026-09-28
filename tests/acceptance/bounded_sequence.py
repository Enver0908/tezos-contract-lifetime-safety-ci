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
        fixtures = project / "fixtures" / "strategy-v1"
        script = fixtures / "bounded_last64.json"
        context = ExecutionContext.from_dict({}, runtime, fixtures)
        input_value = {"prim": "Unit"}

        with OctezRunner(runtime, fixtures, timeout=60) as runner:
            for size, expected_length in ((0, 1), (1, 2), (63, 64), (64, 64), (65, 64), (256, 64)):
                with self.subTest(size=size):
                    boundary = runner.run_code(
                        script,
                        generate_storage("descending_list_nat", size),
                        input_value,
                        context,
                        runtime.hard_gas_limit_per_operation,
                    )
                    self.assertTrue(boundary.succeeded, boundary.raw_stderr_tail)
                    self.assertIsInstance(boundary.storage, list)
                    self.assertEqual(len(boundary.storage), expected_length)
                    expected_values = [1, *range(size, max(size - 63, 0), -1)]
                    self.assertEqual(boundary.storage, [{"int": str(value)} for value in expected_values])

            first = runner.run_code(
                script,
                generate_storage("descending_list_nat", 63),
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
            expected_second = [1, 1, *range(63, 1, -1)]
            self.assertEqual(second.storage, [{"int": str(value)} for value in expected_second])

    def test_valid_bounded_states_stay_at_64_and_preserve_latest_history(self) -> None:
        project = Path(__file__).resolve().parents[2]
        runtime = load_runtime(project / "runtime.lock.json")
        fixtures = project / "fixtures" / "strategy-v1"
        script = fixtures / "bounded_last64.json"
        context = ExecutionContext.from_dict({}, runtime, fixtures)
        input_value = {"prim": "Unit"}

        with OctezRunner(runtime, fixtures, timeout=60) as runner:
            for size in (0, 1, 8, 63, 64, 256, 4096):
                with self.subTest(size=size):
                    initial = generate_storage("bounded_descending_list_nat", size)
                    result = runner.run_code(
                        script,
                        initial,
                        input_value,
                        context,
                        runtime.hard_gas_limit_per_operation,
                    )
                    self.assertTrue(result.succeeded, result.raw_stderr_tail)
                    expected_values = [str(value) for value in [1, *[item["int"] for item in initial]][:64]]
                    self.assertEqual(
                        result.storage,
                        [{"int": value} for value in expected_values],
                    )
                    self.assertLessEqual(len(result.storage), 64)

    def test_paired_map_and_allowance_lookups_return_the_same_value(self) -> None:
        project = Path(__file__).resolve().parents[2]
        runtime = load_runtime(project / "runtime.lock.json")
        fixtures = project / "fixtures" / "strategy-v1"
        input_value = {"prim": "Unit"}
        cases = (
            (
                "map_lookup.json",
                "lookup_pair_map_address_nat",
            ),
            (
                "big_map_lookup.json",
                "lookup_pair_big_map_address_nat",
            ),
            (
                "nested_allowance_lookup.json",
                "lookup_nested_address_allowances",
            ),
            (
                "flat_allowance_lookup.json",
                "lookup_pair_big_map_pair_address_address_nat",
            ),
        )

        with OctezRunner(runtime, fixtures, timeout=60) as runner:
            for size in (0, 1, 64, 4096):
                expected_lookup = {"int": "0" if size == 0 else "1"}
                for script_name, storage_generator in cases:
                    with self.subTest(size=size, script=script_name):
                        script = fixtures / script_name
                        storage = generate_storage(storage_generator, size)
                        context = ExecutionContext.from_dict({}, runtime, fixtures)
                        result = runner.run_code(
                            script,
                            storage,
                            input_value,
                            context,
                            runtime.hard_gas_limit_per_operation,
                        )
                        self.assertTrue(result.succeeded, result.raw_stderr_tail)
                        self.assertEqual(result.storage["prim"], "Pair")
                        self.assertEqual(result.storage["args"][1], expected_lookup)
                        self.assertEqual(result.operations, [])
