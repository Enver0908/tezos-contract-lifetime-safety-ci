from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.bounded_sequence import (
    CHECKPOINT_CALLS,
    PROTOCOL_PATH,
    SequenceError,
    _validate_trace,
    collect_transition_chain,
    load_protocol,
    save_new_json,
    _expected_next_call_output,
    _post_saturation_budgets,
)
from tlsci.models import ExecutionContext, ExecutionResult


class FakeBoundedRunner:
    def __init__(self, limit: int = 64) -> None:
        self.limit = limit
        self.calls = 0

    def run_code(self, script, storage, input_value, context, gas_budget):
        self.calls += 1
        updated = [{"int": "1"}, *storage][: self.limit]
        return ExecutionResult(status="success", gas_budget=gas_budget, storage=updated)


class BoundedSequenceTests(unittest.TestCase):
    def test_locked_protocol_pins_existing_contract_and_runtime(self) -> None:
        protocol, runtime, sources = load_protocol()
        self.assertEqual(protocol["checkpoint_calls"], list(CHECKPOINT_CALLS))
        self.assertEqual(runtime.octez_version, "Octez 25.2")
        self.assertEqual(sources["script"].name, "bounded_last64_append.json")

    def test_checkpoint_measurement_is_the_next_call_not_the_checkpoint_input(self) -> None:
        self.assertEqual(_expected_next_call_output(0)["storage"], [{"int": "1"}])
        self.assertEqual(len(_expected_next_call_output(63)["storage"]), 64)
        self.assertEqual(len(_expected_next_call_output(64)["storage"]), 64)
        self.assertEqual(len(_expected_next_call_output(4096)["storage"]), 64)

    def test_plateau_budget_report_uses_canonical_json_string_keys(self) -> None:
        measurements = {
            calls: {"minimum_successful_gas_budget": 490}
            for calls in CHECKPOINT_CALLS
        }
        budgets = _post_saturation_budgets(measurements)
        self.assertEqual(budgets, {"64": 490, "65": 490, "256": 490, "1024": 490, "4096": 490})

    def test_complete_chain_feeds_each_output_to_next_input_and_caps_storage(self) -> None:
        _, runtime, _ = load_protocol()
        context = ExecutionContext.from_dict({}, runtime)
        runner = FakeBoundedRunner(limit=4)
        progress = []
        trace, snapshots = collect_transition_chain(
            runner,
            PROTOCOL_PATH.parent.parent / "growth-v2" / "contracts" / "synthetic" / "bounded_last64_append.json",
            runtime,
            context,
            {"prim": "Unit"},
            maximum_calls=8,
            storage_limit=4,
            checkpoints=(0, 1, 4, 8),
            maximum_seconds=30,
            progress=lambda current, total: progress.append((current, total)),
        )
        self.assertEqual(runner.calls, 8)
        self.assertEqual([len(snapshots[index]) for index in (0, 1, 4, 8)], [0, 1, 4, 4])
        self.assertEqual(trace["steps"][4]["input_storage_sha256"], trace["steps"][3]["storage_after_sha256"])
        self.assertEqual(trace["steps"][-1]["completed_calls_after"], 8)
        self.assertEqual(progress, [(8, 8)])

    def test_every_one_of_4096_states_is_validated_against_the_locked_trace(self) -> None:
        protocol, runtime, sources = load_protocol()
        context = ExecutionContext.from_dict({}, runtime)
        runner = FakeBoundedRunner()
        trace, _ = collect_transition_chain(
            runner,
            sources["script"],
            runtime,
            context,
            {"prim": "Unit"},
        )
        cases = [SimpleNamespace(size=size, sequence_index=index) for index, size in enumerate(CHECKPOINT_CALLS)]
        errors, checkpoints = _validate_trace(
            trace,
            protocol,
            runtime,
            sources["script"],
            {"prim": "Unit"},
            context,
            cases,
        )
        self.assertEqual(errors, [])
        self.assertEqual(runner.calls, 4096)
        self.assertEqual(set(checkpoints), set(CHECKPOINT_CALLS))
        self.assertEqual(checkpoints[4096]["storage_cardinality"], 64)

    def test_trace_rejects_a_missing_transition(self) -> None:
        protocol, runtime, sources = load_protocol()
        context = ExecutionContext.from_dict({}, runtime)
        trace, _ = collect_transition_chain(
            FakeBoundedRunner(), sources["script"], runtime, context, {"prim": "Unit"}
        )
        trace["steps"].pop(100)
        cases = [SimpleNamespace(size=size, sequence_index=index) for index, size in enumerate(CHECKPOINT_CALLS)]
        with self.assertRaisesRegex(SequenceError, "all 4096"):
            _validate_trace(trace, protocol, runtime, sources["script"], {"prim": "Unit"}, context, cases)

    def test_trace_rejects_a_broken_state_link_even_when_call_count_is_unchanged(self) -> None:
        protocol, runtime, sources = load_protocol()
        context = ExecutionContext.from_dict({}, runtime)
        trace, _ = collect_transition_chain(
            FakeBoundedRunner(), sources["script"], runtime, context, {"prim": "Unit"}
        )
        trace["steps"][100]["input_storage_sha256"] = "0" * 64
        cases = [SimpleNamespace(size=size, sequence_index=index) for index, size in enumerate(CHECKPOINT_CALLS)]
        with self.assertRaisesRegex(SequenceError, "transition 101 mismatch: input_storage_sha256"):
            _validate_trace(trace, protocol, runtime, sources["script"], {"prim": "Unit"}, context, cases)

    def test_evidence_json_writer_refuses_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            save_new_json(path, {"first": True})
            with self.assertRaises(FileExistsError):
                save_new_json(path, {"first": False})


if __name__ == "__main__":
    unittest.main()
