from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.verify_hosted_evidence import HostedEvidenceError, verify_hosted_evidence


COMMIT = "a" * 40
RUN_ID = "123456789"
WORKFLOW = "owner/repo/.github/workflows/bounded-sequence-evidence.yml@refs/heads/main"
RUNNERS = ("one", "two", "three")
CALLS = [0, 1, 8, 63, 64, 65, 256, 1024, 4096]
CARDINALITIES = [0, 1, 8, 63, 64, 64, 64, 64, 64]


def _bounded() -> dict:
    return {
        "technical_mvp_bounded_go": "go",
        "coverage_complete": True,
        "two_pass_reproducibility": True,
        "transition_count": 4096,
        "checkpoint_count": 9,
        "issues": [],
        "manifest_sha256": "b" * 64,
        "state_trace_sha256": "c" * 64,
        "checkpoints": [
            {"completed_calls": call, "storage_cardinality": count, "minimum_successful_gas_budget": 435 if call == 0 else 490}
            for call, count in zip(CALLS, CARDINALITIES)
        ],
        "direct_replays": [
            {"completed_calls": call, "minimum_succeeded": True, "lower_status": "gas_exhausted"}
            for call in CALLS
        ],
        "post_saturation_budgets": {key: 490 for key in ("64", "65", "256", "1024", "4096")},
    }


def _go() -> dict:
    bounded = _bounded()
    growth = {
        "coverage_complete": True,
        "two_pass_reproducibility": True,
        "positive_growth_controls_pass": True,
        "unrelated_ledger_negative_control_pass": True,
        "historical_acceptance_result": "measured_technical_criteria_not_met",
    }
    return {
        "experiment_id": "technical-mvp-go-v3",
        "technical_mvp_go": "go",
        "open_issues": [],
        "bounded_lifetime_sequence_v1": {
            "fresh_runs_match": True,
            "manifest_hash_match": True,
            "full_transition_trace_match": True,
            "checkpoint_and_gas_match": True,
            "direct_boundary_replays_match": True,
            "run_a": bounded,
            "run_b": bounded,
        },
        "growth_v2": growth,
    }


def _make_root(root: Path) -> None:
    for runner_id in RUNNERS:
        base = root / f"bounded-sequence-v1-{runner_id}"
        (base / "run-a/corpus").mkdir(parents=True)
        (base / "run-a/measurement").mkdir(parents=True)
        (base / "run-b/corpus").mkdir(parents=True)
        (base / "run-b/measurement").mkdir(parents=True)
        (base / "growth-v2").mkdir(parents=True)
        metadata = {
            "schema_version": 1,
            "runner_id": runner_id,
            "commit_sha": COMMIT,
            "workflow_run_id": RUN_ID,
            "workflow_run_attempt": 1,
            "workflow_ref": WORKFLOW,
            "event_name": "workflow_dispatch",
        }
        (base / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (base / "technical-mvp-go-v3.json").write_text(json.dumps(_go()), encoding="utf-8")
        for relative in (
            "run-a/corpus/manifest.json",
            "run-a/corpus/state-trace.json",
            "run-a/measurement/report.json",
            "run-a/bounded-acceptance.json",
            "run-b/corpus/manifest.json",
            "run-b/corpus/state-trace.json",
            "run-b/measurement/report.json",
            "run-b/bounded-acceptance.json",
            "growth-v2/report.json",
            "growth-v2/acceptance.json",
        ):
            (base / relative).write_text("{}", encoding="utf-8")


class HostedEvidenceTests(unittest.TestCase):
    def test_verifies_three_runner_identity_and_normalized_report_families(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_root(root)
            with patch("tools.verify_hosted_evidence.compare_report_data", return_value=(True, "d" * 64)) as compare:
                result = verify_hosted_evidence(
                    root,
                    commit_sha=COMMIT,
                    run_id=RUN_ID,
                    run_attempt=1,
                    workflow_ref=WORKFLOW,
                    reproduce_result="success",
                )
            self.assertEqual(result["hosted_technical_go"], "go")
            self.assertEqual(result["runner_ids"], list(RUNNERS))
            self.assertEqual(compare.call_count, 3)
            self.assertEqual(len(result["evidence_sha256"]), 64)

    def test_rejects_unsuccessful_matrix_job(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(HostedEvidenceError, "matrix job result"):
                verify_hosted_evidence(
                    Path(temp), commit_sha=COMMIT, run_id=RUN_ID, run_attempt=1,
                    workflow_ref=WORKFLOW, reproduce_result="failure",
                )

    def test_rejects_runner_commit_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_root(root)
            metadata_path = root / "bounded-sequence-v1-two/metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["commit_sha"] = "e" * 40
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with patch("tools.verify_hosted_evidence.compare_report_data", return_value=(True, "d" * 64)):
                with self.assertRaisesRegex(HostedEvidenceError, "source commit differs"):
                    verify_hosted_evidence(
                        root, commit_sha=COMMIT, run_id=RUN_ID, run_attempt=1,
                        workflow_ref=WORKFLOW, reproduce_result="success",
                    )

    def test_rejects_missing_trace_before_report_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_root(root)
            (root / "bounded-sequence-v1-three/run-b/corpus/state-trace.json").unlink()
            with patch("tools.verify_hosted_evidence.compare_report_data", return_value=(True, "d" * 64)) as compare:
                with self.assertRaisesRegex(HostedEvidenceError, "state-trace.json"):
                    verify_hosted_evidence(
                        root, commit_sha=COMMIT, run_id=RUN_ID, run_attempt=1,
                        workflow_ref=WORKFLOW, reproduce_result="success",
                    )
            compare.assert_not_called()

    def test_rejects_growth_control_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_root(root)
            path = root / "bounded-sequence-v1-one/technical-mvp-go-v3.json"
            go = json.loads(path.read_text(encoding="utf-8"))
            go["growth_v2"]["unrelated_ledger_negative_control_pass"] = False
            path.write_text(json.dumps(go), encoding="utf-8")
            with patch("tools.verify_hosted_evidence.compare_report_data", return_value=(True, "d" * 64)):
                with self.assertRaisesRegex(HostedEvidenceError, "negative_control"):
                    verify_hosted_evidence(
                        root, commit_sha=COMMIT, run_id=RUN_ID, run_attempt=1,
                        workflow_ref=WORKFLOW, reproduce_result="success",
                    )


if __name__ == "__main__":
    unittest.main()
