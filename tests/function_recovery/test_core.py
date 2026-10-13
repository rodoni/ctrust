import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from function_recovery import FunctionMap, FunctionRecord, FunctionUnit, UnitState, transition


class FunctionRecoveryTests(unittest.TestCase):
    def make_map(self):
        records = {
            "c:a.c:first": FunctionRecord("c:a.c:first", "first", "slice-001", definition_file="a.c", definition_line=10),
            "c:a.c:second": FunctionRecord("c:a.c:second", "second", "slice-001", definition_file="a.c", definition_line=20),
        }
        return FunctionMap("slice-001", functions=records)

    def test_selects_earliest_planned_function_deterministically(self):
        mapping = self.make_map()
        result = mapping.select_next()
        self.assertIsNotNone(result.unit)
        self.assertEqual(result.unit.function_ids, ("c:a.c:first",))
        self.assertEqual(result.unit.unit_id, "slice-001-fu-001")

    def test_transition_rejects_skip_to_verified(self):
        mapping = self.make_map()
        unit = mapping.select_next().unit
        mapping.units[unit.unit_id] = unit
        errors = transition(mapping, unit.unit_id, UnitState.VERIFIED)
        self.assertEqual(errors[0].code, "invalid_transition")

    def test_verified_function_is_not_selected_again(self):
        mapping = self.make_map()
        mapping.functions["c:a.c:first"] = FunctionRecord(
            "c:a.c:first", "first", "slice-001", definition_file="a.c", definition_line=10,
            state=UnitState.VERIFIED, rust_file="src/first.rs", rust_name="first"
        )
        result = mapping.select_next()
        self.assertEqual(result.unit.function_ids, ("c:a.c:second",))

    def test_verified_without_legacy_reference_requires_reason_and_scope(self):
        mapping = self.make_map()
        mapping.functions["c:a.c:first"] = FunctionRecord(
            "c:a.c:first", "first", "slice-001", definition_file="a.c", definition_line=10,
            state=UnitState.VERIFIED, rust_file="src/first.rs", rust_name="first",
            comparison_status="verified_without_legacy_reference",
            verification_scope="behavior_contract",
        )
        errors = mapping.validate()
        self.assertTrue(any(error.code == "missing_reference_reason" for error in errors))
        self.assertTrue(any(error.code == "missing_reference_reason" for error in errors))

    def test_parity_requires_executable_reference(self):
        mapping = self.make_map()
        mapping.functions["c:a.c:first"] = FunctionRecord(
            "c:a.c:first", "first", "slice-001", definition_file="a.c", definition_line=10,
            state=UnitState.VERIFIED, rust_file="src/first.rs", rust_name="first",
            comparison_status="parity_verified",
        )
        self.assertTrue(any(error.code == "parity_without_reference" for error in mapping.validate()))

        mapping = self.make_map()
        unit = FunctionUnit("slice-001-fu-001", "slice-001", ("c:a.c:first", "c:a.c:second"), selection="cohesive_group", grouping_reason="shared invariant", context_cost=2)
        mapping.units[unit.unit_id] = unit
        self.assertEqual(mapping.validate(), [])
        self.assertEqual(len(unit.function_ids), 2)


if __name__ == "__main__":
    unittest.main()
