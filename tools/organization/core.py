from .models import Diagnostic, OrganizationInput, OrganizationPlan, OrganizationError, SliceOrganization
from .naming import NAMING_RULE_VERSION, semantic_name

class CtrustOrganizer:
    def plan(self, data: OrganizationInput) -> OrganizationPlan:
        entry = "src/lib.rs" if data.crate_kind == "library" else "src/main.rs"
        shared = "src/type.rs"
        plans = []
        errors = []
        seen_files = {}
        seen_modules = {}
        ordered = sorted(data.slices, key=lambda item: (item.slice_id.casefold(), item.slice_id))
        for item in ordered:
            stem, source = semantic_name(item)
            if not stem:
                errors.append(Diagnostic("missing_semantic_input", item.slice_id, "meaningful objective, behavior, contract or function", "no meaningful tokens", (item.slice_id,), "Provide an explicit semantic name override."))
                continue
            implementation = f"src/{stem}.rs"
            unit_test_file = implementation
            integration_test_file = f"tests/{stem}_integration.rs"
            test_file = integration_test_file
            module = f"crate::{stem}"
            for namespace, value in (("implementation_file", implementation), ("integration_test_file", integration_test_file), ("module_path", module)):
                registry = seen_modules if namespace == "module_path" else seen_files
                key = value.casefold().replace("-", "_")
                if key in registry:
                    other = registry[key]
                    errors.append(Diagnostic("path_collision", item.slice_id, "unique path", value, (other, item.slice_id), "Persist an explicit semantic name override."))
                else:
                    registry[key] = item.slice_id
            plans.append(SliceOrganization(item.slice_id, stem, source, implementation, unit_test_file, integration_test_file, test_file, module, tuple(f.c_name or f.rust_name for f in item.functions), item.test_cases, data.naming_rule_version))
        status = "valid" if not errors else "invalid"
        return OrganizationPlan(data.schema_version, 1, data.naming_rule_version, status, data.unit, data.crate_root, data.crate_kind, entry, shared, tuple(plans), tuple(errors))

    def validate(self, plan: OrganizationPlan) -> tuple[Diagnostic, ...]:
        errors = list(plan.errors)
        if plan.entry_file not in ("src/lib.rs", "src/main.rs"):
            errors.append(Diagnostic("invalid_entry_file", "crate", "src/lib.rs or src/main.rs", plan.entry_file))
        if plan.shared_type_file != "src/type.rs":
            errors.append(Diagnostic("invalid_shared_type_file", "crate", "src/type.rs", plan.shared_type_file))
        for item in plan.slices:
            if not item.test_cases:
                errors.append(Diagnostic("missing_test_assignment", item.slice_id, "at least one test case", "none", (item.slice_id,), "Add characterization or unit test cases."))
        return tuple(errors)
