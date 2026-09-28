import json
import unittest

from tlsci.errors import classify_error, extract_json_values


class ErrorClassificationTests(unittest.TestCase):
    def test_gas_error_is_not_script_rejection(self) -> None:
        status, ids = classify_error(
            "",
            '{"id":"proto.025-PsUshuai.gas_exhausted.operation"}',
            1,
        )
        self.assertEqual(status, "gas_exhausted")

    def test_failwith_text_that_mentions_gas_is_not_gas_exhaustion(self) -> None:
        status, ids = classify_error(
            '[{"id":"proto.alpha.script_rejected"}]',
            'FAILWITH "gas limit exceeded by application rule"',
            1,
        )
        self.assertEqual(status, "script_rejected")
        self.assertIn("proto.alpha.script_rejected", ids)

    def test_structured_gas_exhaustion_wins_over_typecheck_failure_at_low_budget(self) -> None:
        status, ids = classify_error(
            "",
            '[{"id":"proto.025-PsUshuai.michelson_v1.ill_typed_contract"},'
            '{"id":"proto.025-PsUshuai.gas_exhausted.operation"}]',
            1,
        )
        self.assertEqual(status, "gas_exhausted")
        self.assertEqual(len(ids), 2)

    def test_failwith_is_script_rejection(self) -> None:
        status, _ = classify_error(
            "",
            '{"id":"proto.025-PsUshuai.michelson_v1.script_rejected"}',
            1,
        )
        self.assertEqual(status, "script_rejected")

    def test_type_error_is_invalid_fixture(self) -> None:
        status, _ = classify_error(
            "",
            '{"id":"proto.025-PsUshuai.michelson_v1.ill_typed_contract"}',
            1,
        )
        self.assertEqual(status, "type_error")

    def test_json_extraction_handles_large_multiline_output_and_nested_errors(self) -> None:
        payload = {
            "storage": [{"prim": "Elt", "args": [{"int": str(i)}, {"int": "1"}]}
                        for i in range(2000)],
            "errors": [{"id": "proto.025-PsUshuai.gas_exhausted.operation"}],
        }
        text = "client log [info] before JSON\n" + json.dumps(payload, indent=2)
        values = extract_json_values(text)
        self.assertEqual(len(values), 1)
        self.assertEqual(len(values[0]["storage"]), 2000)
        status, ids = classify_error("", text, 1)
        self.assertEqual(status, "gas_exhausted")
        self.assertEqual(ids, ("proto.025-PsUshuai.gas_exhausted.operation",))
