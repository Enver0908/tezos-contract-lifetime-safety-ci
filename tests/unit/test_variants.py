import json
import tempfile
import unittest
from pathlib import Path

from tlsci.variants import make_noop_gas_variant


class VariantTests(unittest.TestCase):
    def test_noop_variant_preserves_declarations_and_prepends_instructions(self) -> None:
        source_data = [
            {"prim": "parameter", "args": [{"prim": "unit"}]},
            {"prim": "storage", "args": [{"prim": "unit"}]},
            {"prim": "code", "args": [[{"prim": "CAR"}]]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            output = root / "variant.json"
            source.write_text(json.dumps(source_data), encoding="utf-8")
            result = make_noop_gas_variant(source, output, 3)
            variant = json.loads(output.read_text(encoding="utf-8"))
            body = variant[2]["args"][0]
            self.assertEqual(result["repetitions"], 3)
            self.assertEqual([item["prim"] for item in body[:6]], ["PUSH", "DROP", "PUSH", "DROP", "PUSH", "DROP"])
            self.assertEqual(body[-1]["prim"], "CAR")

    def test_repetitions_must_be_bounded(self) -> None:
        with self.assertRaises(ValueError):
            make_noop_gas_variant(Path("missing.json"), Path("output.json"), 0)
