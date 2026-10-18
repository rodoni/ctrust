from .models import Diagnostic, OrganizationInput, OrganizationPlan, OrganizationError, SliceOrganization
from .naming import NAMING_RULE_VERSION, semantic_name, tokens


def canonical_rust_name(function):
    value = function.rust_name or function.c_name
    parts = tokens(value)
    if not parts:
        return ""
    name = "_".join(parts)
    return f"m_{name}" if name[0].isdigit() else name


class CtrustOrganizer:
    def plan(self, data: OrganizationInput) -> OrganizationPlan:
        entry = "src/lib.rs" if data.crate_kind == "library" else "src/main.rs"
        shared = "src/type.rs"
        plans = []
        errors = []
        seen_files = {}
        seen_modules = {}
        ordered = sorted(data.slices, key=lambda item: (item.slice_id.casefold(), item.slice_id))
        paths = {}
        functions_by_id = {}
        explicit_by_id = {}
        persisted = data.persisted_mapping or {}
        persisted_symbols = persisted.get("symbols", []) if isinstance(persisted, dict) else []
        persisted_by_id = {item.get("function_id"): item for item in persisted_symbols if isinstance(item, dict) and item.get("function_id")}

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
            paths[item.slice_id] = (implementation, module)
            for namespace, value in (("implementation_file", implementation), ("integration_test_file", integration_test_file), ("module_path", module)):
                registry = seen_modules if namespace == "module_path" else seen_files
                key = value.casefold().replace("-", "_")
                if key in registry:
                    other = registry[key]
                    errors.append(Diagnostic("path_collision", item.slice_id, "unique path", value, (other, item.slice_id), "Persist an explicit semantic name override."))
                else:
                    registry[key] = item.slice_id
            for function in item.functions:
                function_id = function.function_id or f"c:{function.definition_file}:{function.definition_line or ''}:{function.c_name}"
                functions_by_id.setdefault(function_id, []).append((item.slice_id, function))
                candidate = canonical_rust_name(function)
                if function.rust_name:
                    previous = explicit_by_id.setdefault(function_id, (candidate, item.slice_id))
                    if previous[0] != candidate:
                        errors.append(Diagnostic("canonical_name_conflict", function_id, previous[0], candidate, (previous[1], item.slice_id), "Use the same rust_name for this C symbol in every dependent slice."))

        symbol_map = []
        mappings_by_slice = {item.slice_id: [] for item in ordered}
        for function_id in sorted(functions_by_id):
            occurrences = functions_by_id[function_id]
            first_slice, first_function = occurrences[0]
            persisted_item = persisted_by_id.get(function_id, {})
            persisted_name = persisted_item.get("canonical_rust_name", "")
            generated_name = canonical_rust_name(first_function)
            canonical_name = persisted_name or explicit_by_id.get(function_id, (generated_name, first_slice))[0] or generated_name
            if persisted_name and any(function.rust_name and canonical_rust_name(function) != persisted_name for _, function in occurrences):
                errors.append(Diagnostic("canonical_name_conflict", function_id, persisted_name, "changed explicit rust_name", tuple(slice_id for slice_id, _ in occurrences), "Preserve the persisted canonical name or create a deliberate symbol-map migration."))
            owner_slice = persisted_item.get("owner_slice") or first_slice
            if owner_slice not in paths:
                owner_slice = first_slice
            owner_file, owner_module = paths[owner_slice]
            consumers = tuple(slice_id for slice_id, _ in occurrences if slice_id != owner_slice)
            mapping = {
                "function_id": function_id,
                "c_name": first_function.c_name,
                "definition_file": first_function.definition_file,
                "definition_line": first_function.definition_line,
                "canonical_rust_name": canonical_name,
                "owner_slice": owner_slice,
                "rust_file": owner_file,
                "module_path": owner_module,
                "rust_path": f"{owner_module}::{canonical_name}",
                "consumers": list(consumers),
                "reexport_required": bool(consumers),
            }
            symbol_map.append(mapping)
            for slice_id, _ in occurrences:
                mappings_by_slice[slice_id].append(mapping)

        for item in ordered:
            if item.slice_id not in paths:
                continue
            implementation, module = paths[item.slice_id]
            symbols = tuple(mappings_by_slice[item.slice_id])
            plans.append(SliceOrganization(item.slice_id, implementation.removeprefix("src/").removesuffix(".rs"), next((source for source, _ in [(semantic_name(item)[1], item)]), "missing"), implementation, implementation, f"tests/{implementation.removeprefix('src/').removesuffix('.rs')}_integration.rs", f"tests/{implementation.removeprefix('src/').removesuffix('.rs')}_integration.rs", module, tuple(symbol["canonical_rust_name"] for symbol in symbols), symbols, item.test_cases, data.naming_rule_version))
        status = "valid" if not errors else "invalid"
        return OrganizationPlan(data.schema_version, 1, data.naming_rule_version, status, data.unit, data.crate_root, data.crate_kind, entry, shared, tuple(plans), tuple(errors), tuple(symbol_map))

    def validate(self, plan: OrganizationPlan) -> tuple[Diagnostic, ...]:
        errors = list(plan.errors)
        if plan.entry_file not in ("src/lib.rs", "src/main.rs"):
            errors.append(Diagnostic("invalid_entry_file", "crate", "src/lib.rs or src/main.rs", plan.entry_file))
        if plan.shared_type_file != "src/type.rs":
            errors.append(Diagnostic("invalid_shared_type_file", "crate", "src/type.rs", plan.shared_type_file))
        seen = {}
        for symbol in plan.symbol_map:
            key = symbol.get("rust_path")
            if key in seen and seen[key] != symbol.get("function_id"):
                errors.append(Diagnostic("symbol_path_collision", symbol.get("function_id", ""), "unique Rust path", key, (seen[key], symbol.get("function_id", "")), "Assign distinct canonical rust_name values."))
            else:
                seen[key] = symbol.get("function_id")
        for item in plan.slices:
            if not item.test_cases:
                errors.append(Diagnostic("missing_test_assignment", item.slice_id, "at least one test case", "none", (item.slice_id,), "Add characterization or unit test cases."))
        return tuple(errors)
