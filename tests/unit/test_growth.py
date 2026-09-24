import unittest

from tlsci.growth import generate_case, generate_input, generate_storage


class GrowthTests(unittest.TestCase):
    def test_bounded_storage_never_exceeds_64(self) -> None:
        self.assertEqual(len(generate_storage("bounded_list_nat", 0)), 0)
        self.assertEqual(len(generate_storage("bounded_list_nat", 64)), 64)
        self.assertEqual(len(generate_storage("bounded_list_nat", 128)), 64)

    def test_map_keys_are_unique(self) -> None:
        values = generate_storage("map_nat_nat", 100)
        keys = [entry["args"][0]["int"] for entry in values]
        self.assertEqual(len(keys), len(set(keys)))

    def test_input_generators_are_deterministic(self) -> None:
        self.assertEqual(generate_input("bytes32", 7), generate_input("bytes32", 7))
        self.assertEqual(generate_case(type("S", (), {"storage_generator": "list_nat", "input_generator": "unit", "storage_template": None, "input_template": None})(), 4), generate_case(type("S", (), {"storage_generator": "list_nat", "input_generator": "unit", "storage_template": None, "input_template": None})(), 4))
