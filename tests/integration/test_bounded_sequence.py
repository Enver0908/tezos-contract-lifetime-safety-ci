from __future__ import annotations

import unittest

from tlsci.measure import find_minimum_budget
from tlsci.models import ExecutionContext
from tlsci.octez import OctezRunner
from tlsci.util import read_json
from tools.bounded_sequence import PROJECT, collect_transition_chain, load_protocol


class BoundedSequenceIntegrationTests(unittest.TestCase):
    def test_real_octez_sequence_saturates_and_the_next_call_budget_matches(self) -> None:
        protocol, runtime, sources = load_protocol()
        context = ExecutionContext.from_dict(read_json(sources["context"]), runtime, sources["context"].parent)
        input_value = read_json(sources["input"])
        with OctezRunner(runtime, PROJECT, timeout=60) as runner:
            trace, snapshots = collect_transition_chain(
                runner,
                sources["script"],
                runtime,
                context,
                input_value,
                maximum_calls=65,
                storage_limit=protocol["storage_limit"],
                checkpoints=(0, 64, 65),
                maximum_seconds=120,
            )
            at_64 = find_minimum_budget(
                runner, sources["script"], snapshots[64], input_value, context,
                runtime.hard_gas_limit_per_operation,
            )
            at_65 = find_minimum_budget(
                runner, sources["script"], snapshots[65], input_value, context,
                runtime.hard_gas_limit_per_operation,
            )

        self.assertEqual(len(trace["steps"]), 65)
        self.assertEqual(len(snapshots[64]), 64)
        self.assertEqual(snapshots[64], snapshots[65])
        self.assertEqual(at_64[0:2], at_65[0:2])
        self.assertEqual(at_64[0], "success")


if __name__ == "__main__":
    unittest.main()
