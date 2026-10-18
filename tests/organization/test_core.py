import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from organization.core import CtrustOrganizer
from organization.models import FunctionInput, OrganizationInput, SliceInput
from organization.naming import semantic_name


class OrganizationCoreTests(unittest.TestCase):
    def make_input(self, kind="library"):
        return OrganizationInput(
            crate_kind=kind,
            crate_root="crates/demo",
            unit="demo",
            slices=(
                SliceInput("s-001", objective="Flight altitude calculations", functions=(FunctionInput("calc_altitude"),), test_cases=("normal",)),
                SliceInput("s-002", objective="Math basic", functions=(FunctionInput("add"),), test_cases=("normal",)),
            ),
        )

    def test_library_layout_and_semantic_names(self):
        plan = CtrustOrganizer().plan(self.make_input())
        self.assertEqual(plan.entry_file, "src/lib.rs")
        self.assertEqual(plan.shared_type_file, "src/type.rs")
        self.assertEqual(plan.slices[0].implementation_file, "src/flight_altitude_calculations.rs")
        self.assertEqual(plan.slices[0].unit_test_file, "src/flight_altitude_calculations.rs")
        self.assertEqual(plan.slices[0].integration_test_file, "tests/flight_altitude_calculations_integration.rs")
        self.assertEqual(plan.slices[0].test_file, "tests/flight_altitude_calculations_integration.rs")

    def test_slice_id_description_is_used_without_id_prefix(self):
        data = OrganizationInput(
            crate_kind="library",
            crate_root="crates/demo",
            unit="kin",
            slices=(SliceInput("kin-001-velocity-kinematics", test_cases=("normal",)),),
        )
        plan = CtrustOrganizer().plan(data)
        self.assertEqual(plan.slices[0].semantic_name, "velocity_kinematics")
        self.assertEqual(plan.slices[0].implementation_file, "src/velocity_kinematics.rs")
        self.assertNotIn("001", plan.slices[0].implementation_file)

        plan = CtrustOrganizer().plan(self.make_input("binary"))
        self.assertEqual(plan.entry_file, "src/main.rs")

    def test_collision_is_reported(self):
        data = self.make_input()
        data = OrganizationInput(data.crate_kind, data.crate_root, data.unit, (
            SliceInput("s-001", objective="Math basic", test_cases=("normal",)),
            SliceInput("s-002", objective="math-basic", test_cases=("normal",)),
        ))
        plan = CtrustOrganizer().plan(data)
        self.assertEqual(plan.status, "invalid")
        self.assertTrue(any(error.rule == "path_collision" for error in plan.errors))

    def test_shared_symbol_has_one_canonical_name_and_owner_destination(self):
        shared_one = FunctionInput("legacy_helper", "canonical_helper", "c:src/common.c:10:legacy_helper", "src/common.c", 10)
        shared_two = FunctionInput("legacy_helper", "canonical_helper", "c:src/common.c:10:legacy_helper", "src/common.c", 10)
        data = OrganizationInput(
            crate_kind="library",
            crate_root="crates/demo",
            unit="demo",
            slices=(
                SliceInput("s-001", objective="Base behavior", functions=(shared_one,), test_cases=("normal",)),
                SliceInput("s-002", objective="Consumer behavior", functions=(shared_two,), test_cases=("normal",)),
            ),
        )
        plan = CtrustOrganizer().plan(data)
        self.assertEqual(plan.status, "valid")
        self.assertEqual(len(plan.symbol_map), 1)
        symbol = plan.symbol_map[0]
        self.assertEqual(symbol["canonical_rust_name"], "canonical_helper")
        self.assertEqual(symbol["owner_slice"], "s-001")
        self.assertEqual(symbol["rust_file"], "src/base_behavior.rs")
        self.assertEqual(symbol["module_path"], "crate::base_behavior")
        self.assertEqual(symbol["consumers"], ["s-002"])
        self.assertTrue(symbol["reexport_required"])
        self.assertEqual(plan.slices[0].symbols[0]["rust_path"], "crate::base_behavior::canonical_helper")
        self.assertEqual(plan.slices[1].symbols[0]["rust_path"], "crate::base_behavior::canonical_helper")

    def test_conflicting_names_for_same_symbol_are_blocked(self):
        function_id = "c:src/common.c:10:legacy_helper"
        data = OrganizationInput(
            crate_kind="library",
            crate_root="crates/demo",
            unit="demo",
            slices=(
                SliceInput("s-001", objective="Base behavior", functions=(FunctionInput("legacy_helper", "helper_base", function_id),), test_cases=("normal",)),
                SliceInput("s-002", objective="Consumer behavior", functions=(FunctionInput("legacy_helper", "helper_consumer", function_id),), test_cases=("normal",)),
            ),
        )
        plan = CtrustOrganizer().plan(data)
        self.assertEqual(plan.status, "invalid")
        self.assertTrue(any(error.rule == "canonical_name_conflict" for error in plan.errors))

    def test_distinct_symbols_cannot_share_canonical_rust_path(self):
        data = OrganizationInput(
            crate_kind="library",
            crate_root="crates/demo",
            unit="demo",
            slices=(
                SliceInput("s-001", objective="Base behavior", functions=(
                    FunctionInput("first", "same_name", "c:src/a.c:1:first"),
                    FunctionInput("second", "same_name", "c:src/b.c:2:second"),
                ), test_cases=("normal",)),
            ),
        )
        plan = CtrustOrganizer().plan(data)
        errors = CtrustOrganizer().validate(plan)
        self.assertTrue(any(error.rule == "symbol_path_collision" for error in errors))
    def test_objective_does_not_override_meaningful_slice_id(self):
        data = OrganizationInput(
            crate_kind="library",
            crate_root="crates/demo",
            unit="demo",
            slices=(SliceInput(
                "kinematics-004-flight-phases",
                objective="Port altitude related flight phase logic from mi_cinematica.h",
                test_cases=("normal",),
            ),),
        )
        plan = CtrustOrganizer().plan(data)
        self.assertEqual(semantic_name(data.slices[0]), ("flight_phases", "slice_description"))
        self.assertEqual(plan.slices[0].implementation_file, "src/flight_phases.rs")

    def test_explicit_semantic_name_wins_over_objective(self):
        item = SliceInput(
            "kinematics-004-flight-phases",
            objective="A long free-form business objective that must not become a path",
            name_override="phase_altitude",
            test_cases=("normal",),
        )
        self.assertEqual(semantic_name(item), ("phase_altitude", "override"))


if __name__ == "__main__":
    unittest.main()
