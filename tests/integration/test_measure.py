import tempfile
import unittest
from pathlib import Path

from tlsci.measure import find_minimum_budget, measure_scenario
from tlsci.models import ExecutionContext, ExecutionResult, RuntimeLock, ScenarioSpec


class FakeRunner:
    def __init__(self, required=100, budget_dependent=False):
        self.required = required
        self.budget_dependent = budget_dependent
        self.budgets = []

    def run_code(self, script, storage, input_value, context, gas_budget):
        self.budgets.append(gas_budget)
        required = self.required + len(storage) * 3 if isinstance(storage, list) else self.required
        if gas_budget >= required:
            result_storage = "cap-result" if self.budget_dependent and gas_budget == 200 else storage
            return ExecutionResult(status="success", gas_budget=gas_budget, storage=result_storage, operations=[])
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

    def test_one_gas_unit_boundary_does_not_claim_a_zero_budget_failure(self) -> None:
        runner = FakeRunner(required=1)
        result = find_minimum_budget(
            runner,
            Path("unused.json"),
            {"prim": "Unit"},
            {"prim": "Unit"},
            ExecutionContext(chain_id="chain"),
            1,
        )
        self.assertEqual(result[:4], ("success", 1, None, 1))
        self.assertNotIn(0, runner.budgets)

    def test_cap_exhaustion_is_not_named_protocol_exhaustion(self) -> None:
        runner = FakeRunner(required=201)
        result = find_minimum_budget(
            runner,
            Path("unused.json"),
            {"prim": "Unit"},
            {"prim": "Unit"},
            ExecutionContext(chain_id="chain"),
            200,
        )
        self.assertEqual(result[0], "budget_exceeded")
        self.assertIsNone(result[1])

    def test_different_semantics_at_minimum_and_cap_are_invalid(self) -> None:
        runner = FakeRunner(required=100, budget_dependent=True)
        result = find_minimum_budget(
            runner,
            Path("unused.json"),
            {"prim": "Unit"},
            {"prim": "Unit"},
            ExecutionContext(chain_id="chain"),
            200,
        )
        self.assertEqual(result[0], "budget_dependent")
