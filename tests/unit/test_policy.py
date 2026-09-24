import unittest

from tlsci.models import Baseline, Measurement, RuntimeLock
from tlsci.policy import Policy, evaluate_measurements, exit_code_for


RUNTIME = RuntimeLock(
    schema_version=1,
    octez_image="tezos/tezos:octez-v25.2",
    octez_digest="tezos/tezos@sha256:test",
    octez_version="Octez 25.2",
    protocol="protocol",
    chain_id="chain",
    hard_gas_limit_per_operation=1_000_000,
)


def measurement(gas: int, status: str = "success") -> Measurement:
    return Measurement(
        scenario_id="scenario",
        size=1,
        status=status,
        minimum_successful_gas_budget=gas if status == "success" else None,
        last_failed_gas_budget=gas - 1 if status == "success" else None,
        first_successful_gas_budget=gas if status == "success" else None,
        gas_cap=1_000_000,
        storage_bytes=10,
        operations_count=0,
        fixture_sha256="fixture",
        code_sha256="code",
    )


class PolicyTests(unittest.TestCase):
    def test_pass_below_regression_threshold(self) -> None:
        baseline = Baseline(1, RUNTIME.to_dict(), {"scenario:1": {"minimum_successful_gas_budget": 1000}})
        decisions = evaluate_measurements([measurement(1100)], baseline, Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "pass")
        self.assertEqual(exit_code_for(decisions, []), 0)

    def test_regression_requires_both_delta_thresholds(self) -> None:
        baseline = Baseline(1, RUNTIME.to_dict(), {"scenario:1": {"minimum_successful_gas_budget": 1000}})
        decisions = evaluate_measurements([measurement(1200)], baseline, Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "violation")
        self.assertEqual(exit_code_for(decisions, []), 1)

    def test_missing_baseline_is_invalid(self) -> None:
        decisions = evaluate_measurements([measurement(1000)], Baseline(1, RUNTIME.to_dict(), {}), Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "invalid")
        self.assertEqual(exit_code_for(decisions, []), 2)

    def test_failed_measurement_is_invalid(self) -> None:
        decisions = evaluate_measurements([measurement(0, "over_hard_limit")], None, Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "invalid")
        self.assertEqual(exit_code_for(decisions, []), 2)

    def test_reproducibility_failure_is_invalid(self) -> None:
        decisions = evaluate_measurements([measurement(1000)], None, Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "pass")
        self.assertEqual(exit_code_for(decisions, ["invalid: reproducibility mismatch"]), 2)

    def test_fixture_change_is_invalid_against_baseline(self) -> None:
        baseline = Baseline(
            1,
            RUNTIME.to_dict(),
            {"scenario:1": {"minimum_successful_gas_budget": 1000, "fixture_sha256": "old"}},
        )
        decisions = evaluate_measurements([measurement(1000)], baseline, Policy(), RUNTIME)
        self.assertEqual(decisions[0].decision, "invalid")
        self.assertIn("BASELINE_FIXTURE_MISMATCH", decisions[0].rule_ids)
