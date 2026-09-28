import tempfile
import unittest
from pathlib import Path

from tools.build_growth_corpus import PROJECT, _copy_inputs, _tzsafe_contents
from tlsci.util import read_json, sha256_bytes


class GrowthBuilderTests(unittest.TestCase):
    def test_rebuild_uses_tracked_contract_source_and_upstream_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            sources = _copy_inputs(stage)
            for contract_id in ("tzsafe", "fa2"):
                contract = stage / "contracts" / f"{contract_id}.json"
                self.assertEqual(
                    sha256_bytes(contract.read_bytes()),
                    sources[contract_id]["compiled_michelson_sha256"],
                )
                self.assertTrue((stage / "contracts" / sources[contract_id]["license_file"]).is_file())
            copied_template = stage / "templates" / "tzsafe-sign-proposal-content.json"
            tracked_template = PROJECT / "fixtures" / "growth-v2" / "templates" / "tzsafe-sign-proposal-content.json"
            self.assertEqual(copied_template.read_bytes(), tracked_template.read_bytes())
            tracked_sources = read_json(PROJECT / "fixtures" / "growth-v2" / "sources.json")
            self.assertEqual(
                sha256_bytes(copied_template.read_bytes()),
                tracked_sources["derived_fixture_templates"]["tzsafe-sign-proposal-content"]["sha256"],
            )
            self.assertTrue((stage / "contracts" / "synthetic" / "unbounded_list_append.json").is_file())

    def test_tzsafe_sequence_template_is_in_repo_not_prior_output_directory(self) -> None:
        template = PROJECT / "fixtures" / "growth-v2" / "templates" / "tzsafe-sign-proposal-content.json"
        self.assertTrue(template.is_file())
        contents = _tzsafe_contents()
        self.assertEqual(contents[0]["args"][0]["args"][1], {"int": "1"})


if __name__ == "__main__":
    unittest.main()
