import tempfile
import unittest
from pathlib import Path

from tlsci.measure import measure_scenario
from tlsci.models import ExecutionContext, ExecutionResult, RuntimeLock, ScenarioSpec


class FakeRunner:
    def run_code(self, script, storage, input_value, context, gas_budget):
        required = 100 + len(storage) * 3 if isinstance(storage, list) else 100
        if gas_budget >= required:
            return ExecutionResult(status="success", gas_budget=gas_budget, storage=storage, operations=[])
        return ExecutionResult(status="gas_exhausted", gas_budget=gas_budget, error_ids=("gas",))


class MeasurementTests(unittest.TestCase):
    def test_minimum_budget_is_exact_for_fake_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "script.json"
            script.write_text("[{\"prim\":\"parameter\"},{\"prim\":\"storage\"},{\"prim\":\"code\"}]", encoding="utf-8")
            scenario = ScenarioSpec(
                scenario_id="fake",
                script="script.json",
                sizes=(0, 10),
                storage_generator="list_nat",
                input_generator="unit",
                expected="success",
            )
            runtime = RuntimeLock(1, "image", "digest", "version", "protocol", "chain", 1000)
            results = measure_scenario(scenario, runtime, FakeRunner(), root)
            self.assertEqual([item.minimum_successful_gas_budget for item in results], [100, 130])
            self.assertTrue(all(item.status == "success" for item in results))
