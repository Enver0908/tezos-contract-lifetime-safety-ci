import unittest

from tlsci.errors import classify_error


class ErrorClassificationTests(unittest.TestCase):
    def test_gas_error_is_not_script_rejection(self) -> None:
        status, ids = classify_error(
            "",
            '{"id":"proto.025-PsUshuai.gas_exhausted.operation"}',
            1,
        )
        self.assertEqual(status, "gas_exhausted")
        self.assertEqual(ids, ("proto.025-PsUshuai.gas_exhausted.operation",))

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
