from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.verify_technical_go_v3 import verify_technical_go_v3


def _growth_result() -> dict:
    return {
        "coverage_complete": True,
        "two_pass_reproducibility": True,
        "semantic_postconditions": "pass",
        "result": "measured_technical_criteria_not_met",
        "growth_acceptance": {
            "unbounded_and_both_real_contract_growth_pass": True,
            "unrelated_ledger_negative_control_does_not_cross_growth_signal": True,
            "comparisons": [{
                "scenario_id": "bounded_last64_append",
                "from_size": 64,
                "to_size": 4096,
                "exceeds_both_project_signal_thresholds": True,
            }],
        },
    }


def _bounded(go: str = "go", budget: int = 10) -> dict:
    return {
        "technical_mvp_bounded_go": go,
        "manifest_sha256": "manifest",
        "state_trace_sha256": "trace",
        "checkpoints": [{"completed_calls": 64, "minimum_successful_gas_budget": budget}],
        "post_saturation_budgets": {"64": budget, "65": budget, "256": budget, "1024": budget, "4096": budget},
    }


class TechnicalGoV3Tests(unittest.TestCase):
    @patch("tools.verify_technical_go_v3.verify_bounded_artifacts")
    @patch("tools.verify_technical_go_v3.verify_growth_v2")
    def test_v3_go_requires_two_matching_clean_sequence_runs(self, growth, bounded):
        growth.return_value = _growth_result()
        bounded.side_effect = [_bounded(), _bounded()]
        result = verify_technical_go_v3(
            Path("growth.json"), Path("run-a-corpus"), Path("run-a.json"), Path("run-b-corpus"), Path("run-b.json")
        )
        self.assertEqual(result["technical_mvp_go"], "go")
        self.assertTrue(result["bounded_lifetime_sequence_v1"]["fresh_runs_match"])
        self.assertTrue(growth.called)
        self.assertEqual(bounded.call_count, 2)

    @patch("tools.verify_technical_go_v3.verify_bounded_artifacts")
    @patch("tools.verify_technical_go_v3.verify_growth_v2")
    def test_v3_rejects_different_fresh_run_gas(self, growth, bounded):
        growth.return_value = _growth_result()
        bounded.side_effect = [_bounded(budget=10), _bounded(budget=11)]
        result = verify_technical_go_v3(
            Path("growth.json"), Path("run-a-corpus"), Path("run-a.json"), Path("run-b-corpus"), Path("run-b.json")
        )
        self.assertEqual(result["technical_mvp_go"], "no-go")
        self.assertFalse(result["bounded_lifetime_sequence_v1"]["fresh_runs_match"])

    @patch("tools.verify_technical_go_v3.verify_bounded_artifacts")
    @patch("tools.verify_technical_go_v3.verify_growth_v2")
    def test_v3_keeps_nonbounded_growth_controls_as_gates(self, growth, bounded):
        invalid = _growth_result()
        invalid["growth_acceptance"]["unrelated_ledger_negative_control_does_not_cross_growth_signal"] = False
        growth.return_value = invalid
        bounded.return_value = _bounded()
        with self.assertRaisesRegex(ValueError, "non-bounded controls"):
            verify_technical_go_v3(
                Path("growth.json"), Path("run-a-corpus"), Path("run-a.json"), Path("run-b-corpus"), Path("run-b.json")
            )
        bounded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
