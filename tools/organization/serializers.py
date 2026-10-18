import json
from dataclasses import asdict
from .models import OrganizationPlan


def scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def yaml_lines(value, indent=0):
    prefix = " " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                if not item:
                    lines.append(f"{prefix}{key}: {'[]' if isinstance(item, list) else '{}'}")
                else:
                    lines.append(f"{prefix}{key}:")
                    lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                first = True
                for key, child in item.items():
                    marker = "- " if first else "  "
                    if isinstance(child, (dict, list)):
                        if not child:
                            lines.append(f"{prefix}{marker}{key}: {'[]' if isinstance(child, list) else '{}'}")
                        else:
                            lines.append(f"{prefix}{marker}{key}:")
                            lines.extend(yaml_lines(child, indent + 4))
                    else:
                        lines.append(f"{prefix}{marker}{key}: {scalar(child)}")
                    first = False
            else:
                lines.append(f"{prefix}- {scalar(item)}")
    return lines


def plan_to_dict(plan: OrganizationPlan) -> dict:
    return {
        "version": plan.schema_version,
        "organization_schema": plan.organization_schema,
        "naming_rule": plan.naming_rule_version,
        "status": plan.status,
        "unit": plan.unit,
        "crate": {
            "root": plan.crate_root,
            "kind": plan.crate_kind,
            "entry_file": plan.entry_file,
            "shared_type_file": plan.shared_type_file,
            "crate_relative": True,
        },
        "shared_files": [
            {"role": "entry", "path": plan.entry_file},
            {"role": "shared_types", "path": plan.shared_type_file},
        ],
        "symbols": [dict(symbol) for symbol in plan.symbol_map],
        "slices": [
            {
                "id": item.slice_id,
                "semantic_name": item.semantic_name,
                "semantic_source": item.semantic_source,
                "implementation_file": item.implementation_file,
                "unit_test_file": item.unit_test_file,
                "integration_test_file": item.integration_test_file,
                "test_file": item.test_file,
                "module_path": item.module_path,
                "naming_rule": item.naming_rule,
                "functions": list(item.functions),
                "symbols": [dict(symbol) for symbol in item.symbols],
                "test_cases": list(item.test_cases),
            }
            for item in plan.slices
        ],
        "validation": {
            "errors": [asdict(error) for error in plan.errors],
        },
    }


def plan_to_yaml(plan: OrganizationPlan) -> str:
    return "\n".join(yaml_lines(plan_to_dict(plan))) + "\n"
