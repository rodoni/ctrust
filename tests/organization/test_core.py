import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

from organization.core import CtrustOrganizer
from organization.models import FunctionInput, OrganizationInput, SliceInput


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


if __name__ == "__main__":
    unittest.main()
