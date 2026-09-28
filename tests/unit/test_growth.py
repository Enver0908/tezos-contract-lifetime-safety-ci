import unittest

from tlsci.growth import generate_big_map_literal, generate_case, generate_input, generate_storage


class GrowthTests(unittest.TestCase):
    def test_bounded_storage_never_exceeds_64(self) -> None:
        self.assertEqual(len(generate_storage("bounded_list_nat", 0)), 0)
        self.assertEqual(len(generate_storage("bounded_list_nat", 64)), 64)
        self.assertEqual(len(generate_storage("bounded_list_nat", 128)), 64)

    def test_map_keys_are_unique(self) -> None:
        values = generate_storage("map_nat_nat", 100)
        keys = [entry["args"][0]["int"] for entry in values]
        self.assertEqual(len(keys), len(set(keys)))

    def test_descending_sequence_has_distinct_stable_values(self) -> None:
        self.assertEqual(
            generate_storage("descending_list_nat", 4),
            [{"int": "4"}, {"int": "3"}, {"int": "2"}, {"int": "1"}],
        )

    def test_input_generators_are_deterministic(self) -> None:
        self.assertEqual(generate_input("bytes32", 7), generate_input("bytes32", 7))
        self.assertEqual(generate_case(type("S", (), {"storage_generator": "list_nat", "input_generator": "unit", "storage_template": None, "input_template": None})(), 4), generate_case(type("S", (), {"storage_generator": "list_nat", "input_generator": "unit", "storage_template": None, "input_template": None})(), 4))

    def test_valid_bounded_descending_state_stops_at_64(self) -> None:
        self.assertEqual(generate_storage("bounded_descending_list_nat", 0), [])
        self.assertEqual(generate_storage("bounded_descending_list_nat", 3), [
            {"int": "3"}, {"int": "2"}, {"int": "1"}
        ])
        full = generate_storage("bounded_descending_list_nat", 4096)
        self.assertEqual(len(full), 64)
        self.assertEqual(full[0], {"int": "64"})
        self.assertEqual(full[-1], {"int": "1"})

    def test_size_specific_big_map_literals_are_deterministic_and_typed(self) -> None:
        self.assertEqual(generate_big_map_literal("nat_nat", 0), [])
        self.assertEqual(generate_big_map_literal("nat_nat", 2), [
            {"prim": "Elt", "args": [{"int": "0"}, {"int": "1"}]},
            {"prim": "Elt", "args": [{"int": "1"}, {"int": "1"}]},
        ])
        pair_entry = generate_big_map_literal("pair_nat_nat_nat", 1)[0]
        self.assertEqual(pair_entry["args"][0], {
            "prim": "Pair", "args": [{"int": "0"}, {"int": "0"}]
        })
        address_entries = generate_big_map_literal("address_nat", 16)
        addresses = [entry["args"][0]["string"] for entry in address_entries]
        self.assertEqual(addresses, sorted(addresses))
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_lookup_storage_generators_include_result_slot(self) -> None:
        nested = generate_storage("lookup_nested_allowances", 1)
        self.assertEqual(nested["prim"], "Pair")
        self.assertEqual(nested["args"][1], {"int": "0"})
        self.assertEqual(nested["args"][0][0]["args"][1], [
            {"prim": "Elt", "args": [{"int": "0"}, {"int": "1"}]}
        ])
