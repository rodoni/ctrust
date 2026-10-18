"""Structured artifact handling shared by the controller and migration gates."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from importlib import metadata
from datetime import datetime, timezone
from pathlib import Path

import yaml

from artifact_sync.transaction import WorkspaceLock, atomic_write, TransactionError
from function_recovery import UnitState, ALLOWED_TRANSITIONS
from workflow_stages import STAGES, ACTION_REQUIREMENTS, FUNCTION_ACTIONS
from manifest_schema import ManifestSchemaError, normalize_slice


class WorkflowError(ValueError):
    pass


class ExecutionError(WorkflowError):
    pass


class Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def represent_list(dumper, values):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", values, flow_style=all(not isinstance(v, (dict, list)) for v in values))


Dumper.add_representer(list, represent_list)


class Loader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise WorkflowError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load(path):
    if not path.is_file():
        raise WorkflowError(f"Artifact not found: {path}")
    try:
        result = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)
    except WorkflowError as exc:
        raise WorkflowError(f"Invalid YAML in {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise WorkflowError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise WorkflowError(f"Expected YAML mapping: {path}")
    return result


def save(root, path, data):
    if path.name == "migration.yaml":
        data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write(root, str(path.relative_to(root)), yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True).encode())


STRUCTURE_FIELDS = ("structures_identified", "fields_mapped", "layout_mapped", "pointer_semantics_mapped", "shared_memory_classified", "rust_strategy_defined", "evidence_recorded")
BEHAVIOR_FIELDS = ("source_logic_read", "branches_mapped", "calls_mapped", "errors_mapped", "side_effects_mapped", "test_cases_defined")
STATES = {s.value for s in UnitState}
MIGRATION_ROLES = {"port", "adapter", "reference", "simulate", "exclude"}


def is_non_port_role(fn):
    return fn.get("migration_role", "port") in {"adapter", "reference", "simulate", "exclude"}


def require_non_port_decision(fn):
    role = fn.get("migration_role", "port")
    if role not in MIGRATION_ROLES:
        raise WorkflowError(f"Invalid migration_role for {fn.get('c_name')}: {role}")
    if role != "port" and not fn.get("migration_decision"):
        raise WorkflowError(f"Missing migration_decision for non-port function: {fn.get('c_name')}")


def confirmation_errors(path, fields=()):
    if not path.is_file():
        return [f"{path}: missing artifact"]
    data = load(path)
    errors = []
    completion = data.get("completion", {})
    if not isinstance(completion, dict):
        errors.append(f"{path}: completion expected mapping, observed {completion!r}")
        completion = {}
    root_status = data.get("status")
    nested_status = completion.get("status")
    if root_status is not None and nested_status is not None and root_status != nested_status:
        errors.append(f"{path}: conflicting confirmation status, root status={root_status!r}, completion.status={nested_status!r}")
    effective_status = root_status if root_status is not None else nested_status
    if effective_status != "confirmed":
        errors.append(f"{path}: status expected 'confirmed', observed {effective_status!r}; review evidence before confirming")
    for field in fields:
        if completion.get(field) is not True:
            observed = repr(completion[field]) if field in completion else "missing"
            hint = "; found structs_identified; use structures_identified" if field == "structures_identified" and "structs_identified" in completion else ""
            errors.append(f"{path}: completion.{field} expected boolean true, observed {observed}{hint}; complete analysis and evidence before confirming")
    return errors


def confirmed(path, fields=()):
    return not confirmation_errors(path, fields)


def require_confirmed(path, fields=()):
    errors = confirmation_errors(path, fields)
    if errors:
        raise WorkflowError("; ".join(errors))


# Artifacts emitted together by export_inventory.py. Draft analysis is valid
# inventory; confirmation is a separate gate after factual export.
INVENTORY_FIELDS = {
    "manifest.yaml": {"summary": dict},
    "symbols.yaml": {"symbols": list},
    "function-map.yaml": {"functions": list},
    "dependencies.yaml": {"edges": list},
    "callers.yaml": {"edges": list},
    "files.yaml": {"files": list, "headers": list},
    "data-structures.yaml": {"structures": list},
    "mapping-confirmation.yaml": {"counts": dict},
    "behavior.yaml": {"functions": list},
}


def inventory_errors(root, sid, directory=None):
    errors = []
    for name, fields in INVENTORY_FIELDS.items():
        path = (directory if directory is not None else root / f"inventory/slices/{sid}") / name
        try:
            data = load(path)
        except WorkflowError as exc:
            errors.append(str(exc))
            continue
        if "slice" in data and data["slice"] != sid:
            errors.append(f"{path}: slice expected {sid!r}, observed {data['slice']!r}")
        for field, expected in fields.items():
            if not isinstance(data.get(field), expected):
                observed = repr(data[field]) if field in data else "missing"
                errors.append(f"{path}: {field} expected {expected.__name__}, observed {observed}")
    return errors


def require_inventory(root, sid):
    errors = inventory_errors(root, sid)
    if errors:
        raise WorkflowError("Inventory incomplete or invalid; run c-code-inventory for " + sid + ": " + "; ".join(errors))


def doctor(project):
    """Diagnose persistent ctrust state without writing any artifact."""
    project = project.resolve()
    root = project / ".ctrust"
    errors = []
    warnings = []
    state_path = root / "migration.yaml"
    if not state_path.is_file():
        errors.append(f"Missing canonical state: {state_path}")
        return errors, warnings
    try:
        state = load(state_path)
    except WorkflowError as exc:
        return [str(exc)], warnings
    if state.get("version") != 2:
        errors.append(f"{state_path}: version expected 2, observed {state.get('version')!r}")
    shadow = project / "migration.yaml"
    if shadow.is_file():
        warnings.append(f"Shadow migration.yaml exists at project root; ctrust uses {state_path}")
        try:
            root_state = load(shadow)
            for section in ("source", "target", "output"):
                if root_state.get(section) != state.get(section):
                    errors.append(f"Root migration.yaml conflicts with .ctrust/migration.yaml in section {section!r}")
        except WorkflowError as exc:
            errors.append(str(exc))
    try:
        entries = manifests(root)
    except WorkflowError as exc:
        errors.append(str(exc))
        return errors, warnings
    current = state.get("current_unit")
    if current not in entries and current not in (None, "", "none"):
        errors.append(f"current_unit is not declared in the slice manifest: {current}")
    sid = current if current in entries else select_slice(entries, state)
    if not sid:
        warnings.append("No active or eligible slice")
        return errors, warnings
    inventory = root / f"inventory/slices/{sid}"
    errors.extend(inventory_errors(root, sid))
    map_path = inventory / "function-map.yaml"
    if map_path.is_file():
        try:
            _, function_map = read_map(root, sid)
            for fn in function_map["functions"]:
                require_non_port_decision(fn)
                if is_non_port_role(fn):
                    continue
                state_name = fn.get("state")
                if state_name in {"implemented", "tested", "verified"}:
                    rust_file = fn.get("rust_file", "")
                    if not rust_file or not (project / rust_file).is_file():
                        errors.append(f"{sid}/{fn.get('c_name')}: implemented Rust file missing: {rust_file}")
                if state_name in {"tested", "verified"}:
                    refs = fn.get("evidence_refs", [])
                    if not refs:
                        errors.append(f"{sid}/{fn.get('c_name')}: tested function has no evidence_refs")
                    for ref in refs:
                        if not str(ref).endswith(".ndjson"):
                            errors.append(f"{sid}/{fn.get('c_name')}: evidence_ref is not NDJSON: {ref}")
                        elif not (project / ref).is_file():
                            errors.append(f"{sid}/{fn.get('c_name')}: evidence file missing: {ref}")
        except WorkflowError as exc:
            errors.append(str(exc))
    organization = root / "plans/units/organization.yaml"
    if organization.is_file():
        try:
            from organization.cli import validate_persisted
            plan = validate_persisted(project, sid)
            mappings = {item.get("function_id"): item for item in plan.symbol_map}
            for fn in function_map.get("functions", []) if map_path.is_file() else []:
                if is_non_port_role(fn):
                    continue
                if fn.get("state") in {"implemented", "tested", "verified"} and fn.get("function_id") in mappings:
                    mapping = mappings[fn["function_id"]]
                    if mapping.get("owner_slice") != sid:
                        continue
                    expected_file = (Path(plan.crate_root) / mapping["rust_file"]).resolve()
                    observed_file = (project / fn.get("rust_file", "")).resolve()
                    if expected_file != observed_file or fn.get("module_path") != mapping.get("module_path"):
                        errors.append(f"{sid}/{fn.get('c_name')}: function-map destination is stale; expected {expected_file}, {mapping.get('module_path')}, observed {observed_file}, {fn.get('module_path')}")
        except (WorkflowError, OSError) as exc:
            errors.append(f"organization: {exc}")
    return errors, warnings


def require_mapping(path):
    require_confirmed(path)
    counts = load(path).get("counts", {})
    unresolved = counts.get("unresolved") if isinstance(counts, dict) else None
    if type(unresolved) is not int or unresolved != 0:
        raise WorkflowError(f"{path}: counts.unresolved expected integer 0, observed {unresolved!r}; resolve and confirm mappings")


def manifests(root):
    entries = {}
    for path in sorted((root / "plans/units").glob("*-slices.yaml")):
        data = load(path)
        for raw_item in data.get("slices", []):
            try:
                item = normalize_slice(raw_item)
            except ManifestSchemaError as exc:
                raise WorkflowError(str(exc)) from exc
            raw_item.clear()
            raw_item.update(item)
            item = raw_item
            sid = item.get("id")
            if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", sid):
                raise WorkflowError(f"Invalid slice ID in {path}: {sid!r}")
            if sid in entries:
                raise WorkflowError(f"Duplicate slice ID: {sid}")
            if item.get("status", "planned") not in {"planned", "in_progress", "verified", "blocked", "completed"}:
                raise WorkflowError(f"Invalid slice status: {sid}")
            entries[sid] = (item, path, data)
    if not entries:
        raise WorkflowError("No slice manifest or slices found; run migration-scoping")
    visiting, visited = set(), set()

    def visit(sid):
        if sid in visiting:
            raise WorkflowError(f"Dependency cycle involving {sid}")
        if sid in visited:
            return
        visiting.add(sid)
        for dep in entries[sid][0].get("dependencies", []):
            if dep.get("kind", "slice") != "slice":
                continue
            other = dep.get("id")
            if other not in entries:
                raise WorkflowError(f"Unknown dependency {other!r} in slice {sid}")
            visit(other)
        visiting.remove(sid)
        visited.add(sid)
    for sid in entries:
        visit(sid)
    return entries


def eligible(item, entries):
    for dep in item.get("dependencies", []):
        if dep.get("kind", "slice") != "slice" or not dep.get("blocking", True):
            continue
        status = entries[dep["id"]][0].get("status", "planned")
        required = dep.get("required_status", "verified")
        if status != required and not (required == "verified" and status == "completed"):
            return False
    return True


def select_slice(entries, state):
    active = [sid for sid, (item, _, _) in entries.items() if item.get("status") in {"in_progress", "blocked"}]
    if len(active) > 1:
        raise WorkflowError(f"Multiple active slices: {', '.join(active)}")
    if active:
        sid = active[0]
        if not eligible(entries[sid][0], entries):
            raise WorkflowError(f"Active slice {sid} has unsatisfied dependencies")
        return sid
    current = state.get("current_unit")
    if current in entries and entries[current][0].get("status", "planned") == "planned" and eligible(entries[current][0], entries):
        return current
    candidates = [(item.get("order", 0), sid) for sid, (item, _, _) in entries.items() if item.get("status", "planned") == "planned" and eligible(item, entries)]
    if candidates:
        return min(candidates)[1]
    if any(item.get("status", "planned") == "planned" for item, _, _ in entries.values()):
        raise WorkflowError("No eligible slice: blocking prerequisites are unsatisfied")
    return ""


def read_map(root, sid, upgrade=False):
    path = root / f"inventory/slices/{sid}/function-map.yaml"
    data = load(path)
    if data.get("version") != 2 and not upgrade:
        raise WorkflowError(f"Function map requires schema v2: {path}; run function-map {sid}")
    functions = data.get("functions", [])
    if not functions:
        raise WorkflowError(f"Function map has no functions: {path}")
    ids = set()
    for index, fn in enumerate(functions, 1):
        name = fn.get("c_name")
        if not name:
            raise WorkflowError(f"Function without c_name: {path}")
        fn.setdefault("function_id", f"c:{fn.get('definition_file', fn.get('c_definition', ''))}:{fn.get('definition_line', index)}:{name}")
        if fn["function_id"] in ids:
            raise WorkflowError(f"Duplicate function ID: {fn['function_id']}")
        ids.add(fn["function_id"])
        if upgrade:
            fn.setdefault("state", "blocked" if fn.get("status") in {"unresolved", "ambiguous", "indirect"} else "planned")
            for key, default in {"rust_file": "", "module_path": "", "unit_id": "", "evidence_refs": [], "implementation_status": "pending", "test_status": "pending", "comparison_status": "pending"}.items():
                fn.setdefault(key, default)
        if fn.get("state") not in STATES:
            raise WorkflowError(f"Invalid or missing function state: {fn['function_id']}")
    data.update(version=2, slice=sid)
    data.setdefault("revision", 1)
    data.setdefault("units", [])
    return path, data


def active_function(root, state, data, unit):
    if state.get("active_unit") != unit:
        raise WorkflowError(f"Unit {unit} is not the active unit {state.get('active_unit')}")
    ids = state.get("active_function_ids", [])
    functions = [f for f in data["functions"] if f["function_id"] in ids and f.get("unit_id") == unit]
    if len(functions) != 1:
        raise WorkflowError("Active unit must identify exactly one mapped function; select-function first")
    return functions[0]


def set_transition(fn, target):
    if UnitState(target) not in ALLOWED_TRANSITIONS[UnitState(fn["state"])]:
        raise WorkflowError(f"Cannot transition {fn['c_name']} from {fn['state']} to {target}")
    if target == "blocked":
        fn["blocked_from"] = fn["state"]
    fn["state"] = target


def evidence_path(project, value):
    path = Path(value)
    path = (project / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(project):
        raise WorkflowError(f"Evidence/destination escapes project: {value}")
    if not path.is_file():
        if path.exists():
            raise WorkflowError(f"Evidence path is not a regular file: {path}")
        raise WorkflowError(f"Evidence file not found: {path}")
    return path


def rust_destination_path(project, value):
    path = Path(str(value or "").strip())
    if not str(value or "").strip():
        raise WorkflowError("Rust implementation path is required; use 'auto' or pass <rust-file> <module-path>")
    path = (project / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(project):
        raise WorkflowError(f"Rust implementation path escapes project: {value}")
    if not path.is_file():
        raise WorkflowError(f"Rust implementation file not found: {path}; record-implementation expects a Rust file path, not a description or evidence path")
    return path


def evidence_candidates(root, sid, fn):
    names = [fn.get("c_name", ""), fn.get("rust_name", "")]
    candidates = [root / "evidence" / sid / f"{name}.ndjson" for name in names if name]
    unit = fn.get("unit_id", "")
    if unit:
        candidates.append(root / "evidence" / "tests" / f"{unit}.ndjson")
    return list(dict.fromkeys(candidates))


def resolve_evidence_path(project, root, sid, fn, value=""):
    requested = str(value or "").strip()
    names = {fn.get("c_name", ""), fn.get("rust_name", "")}
    automatic = not requested or requested in {"auto", "--auto"} or requested in names
    if automatic:
        candidates = evidence_candidates(root, sid, fn)
        existing = [path for path in candidates if path.is_file()]
        if len(existing) == 1:
            return existing[0]
        if len(existing) > 1:
            paths = ", ".join(str(path.relative_to(project)) for path in existing)
            raise WorkflowError(f"Multiple evidence files found for {fn.get('c_name')}: {paths}; pass the exact NDJSON path")
        expected = str(candidates[0].relative_to(project)) if candidates else f".ctrust/evidence/{sid}/{fn.get('c_name', 'FUNCTION')}.ndjson"
        raise WorkflowError(f"Evidence for {fn.get('c_name')} not found. Create an NDJSON file or pass its path; expected location: {expected}. Usage: record-evidence <unit-id> <evidence-path>")
    try:
        return evidence_path(project, requested)
    except WorkflowError as exc:
        raise WorkflowError(f"{exc}. Usage: record-evidence <unit-id> <evidence-path>; use 'auto' to resolve the active function evidence") from exc


def evidence_records(project, value, sid, unit, function_id):
    path = evidence_path(project, value)
    try:
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise WorkflowError(f"Invalid NDJSON evidence: {path}") from exc
    if not records or any(not isinstance(r, dict) or r.get("result") != "passed" or r.get("slice_id") != sid or r.get("unit_id") != unit or r.get("function_id") != function_id or not r.get("command") or not r.get("expected_source") or "expected" not in r or "observed" not in r or r["expected"] != r["observed"] for r in records):
        raise WorkflowError("Evidence must contain passing, matching expected/observed results, provenance and command for this slice/unit/function")
    return records


def validate_function(project, sid, fn):
    require_non_port_decision(fn)
    if is_non_port_role(fn):
        if fn.get("state") != "verified":
            raise WorkflowError(f"Non-port function is not architecturally verified: {fn['c_name']}")
        return
    if fn["state"] != "verified":
        raise WorkflowError(f"Function not verified: {fn['c_name']}")
    if fn.get("implementation_status") != "implemented" or fn.get("test_status") != "passed":
        raise WorkflowError(f"Verified function lacks completed implementation/tests: {fn['c_name']}")
    evidence_path(project, fn.get("rust_file", ""))
    if not fn.get("module_path") or not fn.get("unit_id") or not fn.get("evidence_refs"):
        raise WorkflowError(f"Verified function lacks destination/unit/evidence: {fn['c_name']}")
    records = []
    for ref in fn["evidence_refs"]:
        records.extend(evidence_records(project, ref, sid, fn["unit_id"], fn["function_id"]))
    validate_comparison(fn, records)


def validate_comparison(fn, records):
    categories = {r.get("category") for r in records}
    if not {"normal", "boundary", "error"} <= categories:
        raise WorkflowError("Comparison evidence must cover normal, boundary and error cases")
    status = fn.get("comparison_status")
    if status == "parity_verified":
        if not all(r.get("executable_harness_available") is True and r.get("expected_source") in {"legacy_execution", "approved_fixture"} for r in records):
            raise WorkflowError("Parity requires executable legacy reference evidence")
    elif status == "verified_without_legacy_reference":
        allowed = {"source_analysis", "domain_rule", "protocol_specification", "manual_calculation", "approved_fixture", "independent_reference_model", "expert_review"}
        if "unresolved_reference" not in categories or fn.get("verification_scope") != "behavior_contract" or not fn.get("legacy_reference", {}).get("reason") or not all(r.get("expected_reason") and r.get("expected_source") in allowed and not any(r.get(k) for k in ("legacy_reference_available", "legacy_tests_available", "executable_harness_available")) for r in records):
            raise WorkflowError("Verification without legacy reference requires a reason, independent expectations and behavior_contract scope")
    else:
        raise WorkflowError("Missing comparison classification")


def stage_errors(root, sid, phase):
    inventory = root / f"inventory/slices/{sid}"
    try:
        if phase == "inventory":
            return inventory_errors(root, sid)
        if phase == "data_structure_mapping":
            errors = confirmation_errors(inventory / "data-structures.yaml", STRUCTURE_FIELDS)
            try:
                require_mapping(inventory / "mapping-confirmation.yaml")
            except WorkflowError as exc:
                errors.append(str(exc))
            return errors
        if phase == "behavior_extraction":
            return confirmation_errors(inventory / "behavior.yaml", BEHAVIOR_FIELDS)
        if phase == "contract_analysis":
            return confirmation_errors(root / f"contracts/units/{sid}.yaml")
        if phase == "modeling":
            return confirmation_errors(root / f"plans/units/{sid}-model.yaml")
        if phase == "organization":
            from organization.cli import validate_persisted
            validate_persisted(root.parent, sid)
            return []
        raise WorkflowError(f"Unknown stage: {phase}")
    except (WorkflowError, ValueError, OSError) as exc:
        return [str(exc)]


def require_action(root, state, sid, command):
    if command not in ACTION_REQUIREMENTS:
        raise WorkflowError(f"Unknown advancing command: {command}")
    if state.get("status") == "blocked" or state.get("attempts", 0) >= state.get("max_attempts", 2):
        raise WorkflowError("Migration blocked; resolve the cause and record a different next action")
    entries = manifests(root)
    if select_slice(entries, state) != sid or entries[sid][0].get("status") == "blocked":
        raise WorkflowError(f"Slice {sid} is not the active eligible slice")
    for stage in STAGES[:ACTION_REQUIREMENTS[command]]:
        errors = stage_errors(root, sid, stage.phase)
        if errors:
            raise WorkflowError(f"Cannot execute {command} for {sid}; required stage {stage.phase}: " + "; ".join(errors))


def prerequisites(root, sid, stage):
    required = {"contract": 3, "model": 4, "organization": 5, "port": 6}
    if stage not in required:
        raise WorkflowError(f"Unknown prerequisite stage: {stage}")
    for item in STAGES[:required[stage]]:
        errors = stage_errors(root, sid, item.phase)
        if errors:
            raise WorkflowError("; ".join(errors))


def inventory_arguments(project, root, state, sid):
    item = manifests(root)[sid][0]
    source = item.get("source", {})
    source_root = source.get("project_root") or state.get("source", {}).get("project_root")
    if not source_root:
        raise WorkflowError(f"{sid}: source.project_root missing; define source before exporting")
    source_path = Path(source_root)
    source_path = (project / source_path).resolve() if not source_path.is_absolute() else source_path.resolve()
    if not source_path.is_dir():
        raise WorkflowError(f"{sid}: C source directory not found: {source_path}")
    headers = source.get("headers", [])
    functions = source.get("functions", [])
    if not headers or not functions:
        raise WorkflowError(f"{sid}: source.headers and source.functions are required; review the slice manifest")
    args = [sys.executable, str(Path(__file__).parent / "header_dependency_analyzer/export_inventory.py"), "--project", str(source_path), "--slice", sid, "--output", str(root / f"inventory/slices/{sid}"), "--cache-dir", str(root / "cache/header_dependency_analyzer")]
    for header in headers:
        path = Path(header)
        path = path if path.is_absolute() else source_path / path
        if not path.is_file():
            raise WorkflowError(f"{sid}: source header not found: {path}")
        args.extend(["--header", str(path)])
    for fn in functions:
        if not isinstance(fn, dict) or not fn.get("c_name"):
            raise WorkflowError(f"{sid}: source.functions entry requires c_name: {fn!r}")
        args.extend(["--function", fn["c_name"]])
    compile_commands = source.get("compile_commands") or state.get("source", {}).get("compile_commands")
    if compile_commands:
        path = Path(compile_commands)
        args.extend(["--compile-commands", str(path if path.is_absolute() else source_path / path)])
    return args


def recovery_for_stage(project, root, state, sid, stage, errors):
    recovery = {"code": stage.recovery_code, "stage": stage.phase, "slice": sid, "action": "review", "automatic": False, "details": errors}
    command = ""
    if stage.phase == "inventory":
        try:
            from inventory_transaction import reviewed_artifacts
            reviewed = reviewed_artifacts(root / f"inventory/slices/{sid}")
            inventory_arguments(project, root, state, sid)
            if reviewed:
                recovery.update(code="inventory_review_required", details=errors + ["Reviewed artifacts preserved: " + ", ".join(reviewed)])
            else:
                recovery.update(action="inventory", automatic=True)
                command = shlex.join([".ctrust/sync.sh", "inventory", ".", sid])
        except (WorkflowError, ValueError, OSError, TransactionError) as exc:
            recovery["details"] = errors + [str(exc)]
    elif stage.phase == "organization":
        # Only a valid calculated plan can be regenerated automatically.
        try:
            from organization.cli import build_plan
            from organization.core import CtrustOrganizer
            calculated = build_plan(project, sid)
            diagnostics = CtrustOrganizer().validate(calculated)
            if diagnostics:
                recovery.update(code="organization_inputs_invalid", details=errors + [f"{item.owner}: {item.rule}" for item in diagnostics])
            else:
                recovery.update(action="organize", automatic=True)
                command = shlex.join([".ctrust/sync.sh", "organize", ".", sid])
        except (WorkflowError, ValueError, OSError) as exc:
            recovery.update(code="organization_inputs_invalid", details=errors + [str(exc)])
    elif stage.phase in {"contract_analysis", "modeling"}:
        action, path = ("contract", root / f"contracts/units/{sid}.yaml") if stage.phase == "contract_analysis" else ("model", root / f"plans/units/{sid}-model.yaml")
        if not path.exists():
            recovery.update(action=action, automatic=True)
            command = shlex.join([".ctrust/sync.sh", action, ".", sid])
    recovery["command"] = command
    return recovery


def reconcile(project, root, state):
    entries = manifests(root)
    if state.get("status") == "blocked" or state.get("attempts", 0) >= state.get("max_attempts", 2):
        print("reconcile: preserved blocker and retry guard; resolve cause and use next explicitly")
        return
    sid = select_slice(entries, state)
    recovery = None
    if not sid:
        state.update(status="in_progress", phase="verification", next_action="Review final evidence and checkpoint before closing the workspace", next_command="", next_skill="migration-verification", active_unit="none", active_functions=[], active_function_ids=[], next_function="none")
    else:
        phase, skill, command = "function_recovery", "c-to-rust-migration", ""
        action = f"Resume next incomplete function in {sid}"
        if entries[sid][0].get("status") == "blocked":
            phase, skill, action = "blocked", "context-recovery", f"Resolve recorded blocker for {sid}"
        else:
            for stage in STAGES:
                errors = stage_errors(root, sid, stage.phase)
                if errors:
                    recovery = recovery_for_stage(project, root, state, sid, stage, errors)
                    phase, skill, command = stage.phase, stage.skill, recovery["command"]
                    action = f"{stage.action} for {sid}: " + "; ".join(recovery["details"])
                    break
            else:
                _, data = read_map(root, sid, upgrade=True)
                if all(f["state"] == "verified" for f in data["functions"]):
                    phase, skill, action = "verification", "migration-verification", f"Run slice gates and checkpoint {sid}"
                    command = shlex.join([".ctrust/sync.sh", "verify-slice", ".", sid])
                else:
                    command = shlex.join([".ctrust/sync.sh", "select-function", ".", sid])
        if state.get("current_unit") != sid:
            state.update(active_unit="none", active_functions=[], active_function_ids=[], next_function="none", active_rust_file="", active_module_path="", active_evidence="", implementation_status="pending", test_status="pending", comparison_status="pending")
        state.update(current_unit=sid, status="blocked" if phase == "blocked" else "in_progress", phase=phase, next_action=action, next_skill=skill, next_command=command)
    state["recovery"] = recovery
    if state["status"] == "blocked":
        state["eligible_actions"] = ["review"]
    elif recovery:
        state["eligible_actions"] = [recovery["action"]] if recovery["automatic"] else ["review"]
    else:
        state["eligible_actions"] = FUNCTION_ACTIONS[state["phase"]] if sid else ["final-review"]
    state["next_owner"] = "ctrust-lead"
    save(root, root / "migration.yaml", state)
    print(f"reconcile: slice={sid or 'none'} phase={state['phase']}")


def recovery_signature(project, root, state, recovery):
    digest = hashlib.sha256(recovery["command"].encode())
    digest.update(sys.version.encode())
    for package in ("networkx", "PyYAML"):
        try:
            digest.update(metadata.version(package).encode())
        except metadata.PackageNotFoundError:
            digest.update(f"missing:{package}".encode())
    paths = set((root / "plans/units").glob("*-slices.yaml"))
    sid = recovery["slice"]
    paths.update((root / f"inventory/slices/{sid}").glob("*.yaml"))
    paths.update([root / f"contracts/units/{sid}.yaml", root / f"plans/units/{sid}-model.yaml"])
    if recovery["action"] == "inventory":
        arguments = inventory_arguments(project, root, state, sid)
        source = Path(arguments[arguments.index("--project") + 1])
        paths.update(path for path in source.rglob("*") if path.is_file() and path.suffix in {".c", ".h", ".json"})
    for path in sorted(paths):
        digest.update(str(path).encode())
        digest.update(path.read_bytes() if path.is_file() else b"missing")
    return digest.hexdigest()


def perform_setup(project, root, state, sid, command):
    require_action(root, state, sid, command)
    if command == "inventory":
        result = subprocess.run(inventory_arguments(project, root, state, sid), text=True, capture_output=True)
        if result.returncode:
            raise ExecutionError(f"Inventory execution failed ({result.returncode}): {result.stderr or result.stdout}")
        print(result.stdout.rstrip())
    elif command == "organize":
        from organization.cli import write_plan, validate_persisted
        write_plan(project, sid)
        validate_persisted(project, sid)
        print(f"organization valid: {sid}")
    else:
        files = [("contract.yaml", f"contracts/units/{sid}.yaml"), ("unit-plan.md", f"plans/units/{sid}.md")] if command == "contract" else [("model.yaml", f"plans/units/{sid}-model.yaml")]
        for template, relative in files:
            path = root / relative
            if path.exists():
                print(f"preserved {path}")
                continue
            content = (Path(__file__).parent.parent / "templates" / template).read_text()
            content = content.replace("UNIT_NAME", sid).replace("SLICE_ID", sid).replace("FUNCTION_NAME", "TODO_FUNCTION")
            atomic_write(root, relative, content.encode())
            print(f"created {path}")
    reconcile(project, root, state)


def run_setup(project, root, state, sid, command, previous_command=None):
    require_action(root, state, sid, command)
    recovery = {"slice": sid, "action": command, "command": shlex.join([".ctrust/sync.sh", command, ".", sid])}
    signature = recovery_signature(project, root, state, recovery)
    if state.get("attempts", 0) > 0 and state.get("failed_recovery_signature") == signature:
        raise WorkflowError("Same recovery already failed with unchanged inputs; correct the cause before retrying")
    prior = {"previous_command": previous_command or state.get("next_command"), "attempts": state.get("attempts", 0), "last_error": state.get("last_error", ""), "corrective_action": command}
    if prior["attempts"] or prior["last_error"]:
        state.setdefault("recovery_history", []).append(prior)
    if state.get("failed_recovery_signature") or (previous_command is not None and previous_command != recovery["command"]):
        state.update(attempts=0, last_error="")
    try:
        perform_setup(project, root, state, sid, command)
    except (ExecutionError, OSError, TransactionError) as exc:
        state.update(attempts=state.get("attempts", 0) + 1, last_error=str(exc), failure_kind="execution", failed_recovery_signature=signature)
        if state["attempts"] >= state.get("max_attempts", 2):
            state["status"] = "blocked"
        save(root, root / "migration.yaml", state)
        raise
    else:
        state.update(attempts=0, last_error="", failure_kind="none")
        state.pop("failed_recovery_signature", None)
        save(root, root / "migration.yaml", state)


def operate(project, command, args):
    root = project / ".ctrust"
    with WorkspaceLock(root):
        state = load(root / "migration.yaml")
        if command == "upgrade-state":
            if state.get("version") == 2:
                print("Migration state already uses schema v2")
                return
            manifests(root)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = f"recovery/migration-legacy-{stamp}.yaml"
            atomic_write(root, backup, (root / "migration.yaml").read_bytes())
            state.update(version=2, status="in_progress", phase="inventory", current_unit="none", active_unit="none", active_functions=[], active_function_ids=[], next_function="none", implementation_status="pending", test_status="pending", comparison_status="pending", function_map_revision=0, next_command="", next_action="Reconcile upgraded state", next_owner="ctrust-lead", next_skill="c-code-inventory", attempts=0, max_attempts=2, last_error="", schema_upgrade_backup=backup)
            state.setdefault("target", {}).setdefault("workspace_root", state.get("target", {}).get("project_root", str(project)))
            save(root, root / "migration.yaml", state)
            print(f"Migration upgraded; original state preserved at {backup}")
            return
        if state.get("version") != 2:
            raise WorkflowError("Migration state requires schema v2; run upgrade-state first")
        if command == "doctor":
            errors, warnings = doctor(project)
            for warning in warnings:
                print(f"DOCTOR WARNING: {warning}")
            for error in errors:
                print(f"DOCTOR ERROR: {error}")
            if errors:
                raise WorkflowError(f"Doctor found {len(errors)} error(s)")
            print("Doctor passed")
            return
        if command == "behavior-function":
            sid = args[0] if args and args[0] not in {"", "checkpoint"} else state.get("current_unit")
            name = args[1] if len(args) > 1 else ""
            if not sid or not name:
                raise WorkflowError("Usage: behavior-function <slice-id> <c-function-name>")
            require_inventory(root, sid)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "function"
            artifact = root / f"inventory/slices/{sid}/behavior/functions/{safe_name}.yaml"
            if not artifact.is_file():
                raise WorkflowError(f"Behavior artifact not found for {name}: {artifact}; run inventory with the scoped exporter first")
            state.update(current_unit=sid, active_unit=f"{sid}-behavior-{safe_name}", active_functions=[name], next_function=name, phase="behavior_extraction", next_skill="c-behavior-extraction", next_action=f"Review and confirm behavior artifact for {name}", next_command="")
            save(root, root / "migration.yaml", state)
            print(artifact.read_text(encoding="utf-8"))
            return
        if command == "guard-action":
            return require_action(root, state, args[0], args[1])
        if command in {"inventory", "contract", "model", "organize"}:
            sid = args[0] if args and args[0] not in {"", "checkpoint"} else state.get("current_unit")
            return run_setup(project, root, state, sid, command)
        if command == "recover":
            if state.get("status") == "blocked" or state.get("attempts", 0) >= state.get("max_attempts", 2):
                raise WorkflowError("Recovery preserves blockers; resolve the cause and use next before recovering")
            previous_action = state.get("next_command")
            reconcile(project, root, state)
            recovery = state.get("recovery")
            if not recovery or not recovery.get("automatic"):
                print("Recovery requires analysis: " + state.get("next_action", ""))
                return
            return run_setup(project, root, state, recovery["slice"], recovery["action"], previous_action)
        if command == "update-state":
            key, value = args
            allowed = {"status", "next_action", "next_command", "attempts", "last_error", "last_checkpoint"}
            if key not in allowed:
                raise WorkflowError(f"Unsupported state field: {key}")
            state[key] = int(value) if key == "attempts" else value
            if key == "status" and value == "blocked":
                state["eligible_actions"] = ["review"]
            save(root, root / "migration.yaml", state)
            return
        if command == "sync-context":
            from artifact_sync.engine import _managed_block
            path = root / "current-context.md"
            start, end = "<!-- ctrust:state:start -->", "<!-- ctrust:state:end -->"
            rows = [("Project", state.get("project", project.name)), ("Phase", state.get("phase")), ("Unit", state.get("current_unit")), ("Status", state.get("status")), ("Active functions", state.get("active_functions", [])), ("Last checkpoint", state.get("last_checkpoint", "none")), ("Next action", state.get("next_action", "")), ("Risk", state.get("risk_level", "unknown"))]
            for section in ("source", "target", "output"):
                rows.extend((f"{section}.{key}", value) for key, value in state.get(section, {}).items() if not isinstance(value, (dict, list)))
            content = start + "\n## ctrust State\n\n" + "".join(f"- {label}: {value}\n" for label, value in rows) + "\nRead the unit plan, contract, model and checkpoint referenced by this state.\n" + end + "\n"
            atomic_write(root, "current-context.md", _managed_block(path, content, start, end).encode())
            return
        if command == "plan-slices":
            template = (Path(__file__).resolve().parent.parent / "templates/slice-plan.md").read_text()
            for sid, (_, _, manifest) in manifests(root).items():
                path = root / f"plans/units/{sid}.md"
                if not path.exists():
                    content = template.replace("UNIT_NAME", str(manifest.get("unit", sid))).replace("SLICE_ID", sid).replace("FUNCTION_NAME", "TODO_FUNCTION")
                    atomic_write(root, str(path.relative_to(root)), content.encode())
                    print(f"created {path}")
            return
        if command == "status-slices":
            entries = manifests(root)
            current = state.get("current_unit")
            counts = dict.fromkeys(("planned", "in_progress", "verified", "blocked", "completed"), 0)
            lines = ["# Slice Migration Status", "", f"- Active slice: {current}", f"- Global phase: {state.get('phase')}", f"- Next action: {state.get('next_action', '')}", f"- Next command: {state.get('next_command', '')}", "", "The slice manifest is the source of truth; this report does not prove equivalence.", "", "| Order | Slice | Status | Active | Active Function | Current stage | Completed | Remaining |", "|---:|---|---|:---:|---|---|---|---|"]
            for sid, (item, _, _) in sorted(entries.items(), key=lambda row: (row[1][0].get("order", 0), row[0])):
                status = item.get("status", "planned")
                counts[status] += 1
                active = sid == current and status not in {"verified", "completed"}
                phase = state.get("phase", "inventory") if active else status
                functions = ", ".join(state.get("active_functions", [])) if active else ""
                done = "gates passed" if status in {"verified", "completed"} else "see checkpoint and function map"
                remaining = "final workspace review" if status == "verified" else "none" if status == "completed" else "resolve blocker" if status == "blocked" else "follow next action; verify-slice after comparison"
                lines.append(f"| {item.get('order', 0)} | {sid} | {status} | {'yes' if active else 'no'} | {functions} | {phase} | {done} | {remaining} |")
            lines.extend(["", "## Summary", "", "| Total | Planned | In progress | Verified | Blocked | Completed |", "|---:|---:|---:|---:|---:|---:|", f"| {sum(counts.values())} | " + " | ".join(str(v) for v in counts.values()) + " |", "", "## Agent protocol", "", "Read the active slice plan, contract, model and checkpoint. Follow NEXT_COMMAND or NEXT_SKILL. Record evidence and use verify-slice to checkpoint and advance.", ""])
            content = "\n".join(lines)
            atomic_write(root, "status/slices.md", content.encode())
            print(content)
            return
        if command == "organization-all":
            from organization.cli import validate_persisted
            for sid in scoped_slices(root, "--all"):
                validate_persisted(project, sid)
            print("Organization gate passed (all slices)")
            return
        if command == "reopen":
            if not args[0] or args[0] == state.get("next_action"):
                raise WorkflowError("Record a different next action after resolving the blocker")
            sid = state.get("current_unit")
            entries = manifests(root)
            if sid in entries:
                item, path, data = entries[sid]
                if item.get("status") == "blocked":
                    item["status"] = "in_progress"
                    save(root, path, data)
                map_path = root / f"inventory/slices/{sid}/function-map.yaml"
                if map_path.exists():
                    _, function_map = read_map(root, sid, upgrade=True)
                    for fn in function_map["functions"]:
                        if fn["state"] == "blocked" and fn["function_id"] in state.get("active_function_ids", []):
                            previous = fn.pop("blocked_from", "in_progress")
                            set_transition(fn, previous if previous in {"in_progress", "implemented", "tested"} else "in_progress")
                    save(root, map_path, function_map)
            return
        if command == "verify-slice":
            sid = args[0] or state.get("current_unit")
            require_action(root, state, sid, command)
            entries = manifests(root)
            if select_slice(entries, state) != sid:
                raise WorkflowError("Only the active eligible slice can be verified")
            prerequisites(root, sid, "port")
            for kind in ("structures", "behavior", "functions", "comparison", "sources"):
                gate(project, kind, sid)
            item, path, data = entries[sid]
            item["status"] = "verified"
            save(root, path, data)
            state.update(status="in_progress", phase="verification", next_command="", next_action=f"Checkpoint verified slice {sid}, then reconcile", next_skill="context-recovery", eligible_actions=["checkpoint"], recovery=None)
            save(root, root / "migration.yaml", state)
            return
        if command == "reconcile":
            return reconcile(project, root, state)
        sid = args[0] if command in {"function-map", "select-function", "prerequisites"} else state.get("current_unit")
        if command == "prerequisites":
            return prerequisites(root, sid, args[1])
        entries = manifests(root)
        if sid not in entries:
            raise WorkflowError(f"Unknown slice: {sid}")
        path, data = read_map(root, sid, upgrade=command in {"function-map", "select-function"})
        if command == "function-map":
            require_action(root, state, sid, command)
            save(root, path, data)
            print(f"function-map valid: slice={sid}, functions={len(data['functions'])}")
            return
        require_action(root, state, sid, command)
        if command == "select-function":
            prerequisites(root, sid, "port")
            unfinished = [f for f in data["functions"] if f["state"] not in {"verified", "blocked"}]
            unfinished.sort(key=lambda f: (f["state"] == "planned", f.get("definition_line") or 0, f["function_id"]))
            if not unfinished:
                raise WorkflowError("No eligible function; resolve blocked functions or verify the slice")
            fn = unfinished[0]
            if not fn.get("unit_id"):
                used = {u["unit_id"] for u in data["units"]} | {f.get("unit_id") for f in data["functions"]}
                index = 1
                while f"{sid}-fu-{index:03d}" in used:
                    index += 1
                fn["unit_id"] = f"{sid}-fu-{index:03d}"
                data["units"].append({"unit_id": fn["unit_id"], "function_ids": [fn["function_id"]]})
            if fn["state"] == "planned":
                set_transition(fn, "in_progress")
                fn["implementation_status"] = "in_progress"
            phase, action = {"in_progress": ("function_implementation", "Implement"), "implemented": ("function_testing", "Test"), "tested": ("function_comparison", "Compare")}[fn["state"]]
            state.update(current_unit=sid, active_unit=fn["unit_id"], active_functions=[fn["c_name"]], active_function_ids=[fn["function_id"]], next_function=fn["c_name"], active_rust_file=fn.get("rust_file", ""), active_module_path=fn.get("module_path", ""), implementation_status=fn.get("implementation_status", "pending"), test_status=fn.get("test_status", "pending"), comparison_status=fn.get("comparison_status", "pending"), phase=phase, status="in_progress", next_command="", next_skill="c-to-rust-migration" if fn["state"] == "in_progress" else "migration-verification", next_action=f"{action} {fn['c_name']} for {fn['unit_id']}")
            item, manifest, manifest_data = entries[sid]
            item["status"] = "in_progress"
            save(root, manifest, manifest_data)
        else:
            fn = active_function(root, state, data, args[0])
            if command == "record-implementation":
                if len(args) != 3:
                    raise WorkflowError("Usage: record-implementation <unit-id> [<rust-file>|auto] <module-path>")
                from organization.cli import validate_persisted
                plan = validate_persisted(project, sid)
                mapping = next((item for item in plan.symbol_map if item.get("function_id") == fn["function_id"]), None)
                if mapping is None:
                    raise WorkflowError(f"No canonical symbol mapping for {fn['function_id']}; regenerate organization")
                expected_file = (Path(plan.crate_root) / mapping["rust_file"]).resolve()
                expected_module = mapping["module_path"]
                requested_file = args[1]
                destination = expected_file if requested_file in {"", "auto", "--auto"} else rust_destination_path(project, requested_file)
                if destination != expected_file or args[2] != expected_module:
                    raise WorkflowError(f"Implementation must follow canonical symbol plan: {expected_file}, {expected_module}")
                set_transition(fn, "implemented")
                fn.update(rust_name=mapping["canonical_rust_name"], rust_file=str(destination.relative_to(project)), module_path=expected_module, rust_path=mapping["rust_path"], implementation_status="implemented")
                state.update(implementation_status="implemented", active_rust_file=fn["rust_file"], active_module_path=expected_module, phase="function_testing", next_action=f"Run tests and record evidence for {args[0]}", next_skill="migration-verification", next_command="")
            elif command == "record-evidence":
                if fn.get("implementation_status") == "implemented":
                    evidence_path(project, fn.get("rust_file", ""))
                evidence = resolve_evidence_path(project, root, sid, fn, args[1] if len(args) > 1 else "")
                evidence_records(project, str(evidence), sid, args[0], fn["function_id"])
                set_transition(fn, "tested")
                fn.setdefault("evidence_refs", []).append(str(evidence.relative_to(project)))
                fn["test_status"] = "passed"
                state.update(test_status="passed", active_evidence=str(evidence.relative_to(project)), phase="function_comparison", next_action=f"Compare and classify evidence for {args[0]}", next_skill="migration-verification", next_command="")
            elif command == "compare":
                if len(args) != 2:
                    raise WorkflowError("Usage: compare <unit-id> <passed|failed>; the active function is selected by select-function")
                if args[1] not in {"passed", "failed"}:
                    raise WorkflowError("Comparison result must be passed or failed")
                if args[1] == "failed":
                    set_transition(fn, "blocked")
                    fn["comparison_status"] = "failed"
                    state.update(status="blocked", comparison_status="failed", next_command="", next_action=f"Resolve failed comparison for {args[0]}")
                    item, manifest, manifest_data = entries[sid]
                    item["status"] = "blocked"
                    save(root, manifest, manifest_data)
                else:
                    records = []
                    for ref in fn.get("evidence_refs", []):
                        records.extend(evidence_records(project, ref, sid, args[0], fn["function_id"]))
                    parity = bool(records) and all(r.get("executable_harness_available") is True for r in records)
                    fn["comparison_status"] = "parity_verified" if parity else "verified_without_legacy_reference"
                    fn["verification_scope"] = "legacy_parity" if parity else "behavior_contract"
                    reference = fn.setdefault("legacy_reference", {})
                    reference.update(available=any(r.get("legacy_reference_available") is True for r in records), tests_available=any(r.get("legacy_tests_available") is True for r in records), executable_harness_available=parity)
                    if not parity:
                        reason = next((r.get("expected_reason") for r in records if r.get("expected_reason")), "")
                        fn.setdefault("legacy_reference", {})["reason"] = reason
                    validate_comparison(fn, records)
                    set_transition(fn, "verified")
                    state.update(comparison_status=fn["comparison_status"], phase="function_recovery", next_action=f"Select next incomplete function or verify {sid}", next_command="", next_skill="c-to-rust-migration")
                    complete = all(f["state"] == "verified" for f in data["functions"])
                    save(root, root / f"inventory/slices/{sid}/comparison.yaml", {"version": 1, "slice": sid, "status": "passed" if complete else "in_progress", "functions": [{"function_id": f["function_id"], "comparison_status": f.get("comparison_status", "pending"), "evidence_refs": f.get("evidence_refs", [])} for f in data["functions"]]})
                    if complete:
                        state.update(phase="verification", next_skill="migration-verification", next_action=f"Run migration-gate for {sid}, then checkpoint and mark slice verified", next_command=shlex.join([".ctrust/sync.sh", "verify-slice", ".", sid]))
                    else:
                        state["next_command"] = shlex.join([".ctrust/sync.sh", "select-function", ".", sid])
        data["revision"] += 1
        save(root, path, data)
        state["function_map_revision"] = data["revision"]
        state["eligible_actions"] = ["review"] if state.get("status") == "blocked" else FUNCTION_ACTIONS[state["phase"]]
        state["recovery"] = None
        save(root, root / "migration.yaml", state)
        audit = root / "logs/progress.ndjson"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": state["updated_at"], "event": command, "slice": sid, "unit": state.get("active_unit"), "phase": state.get("phase")}) + "\n")
        print(f"{command}: slice={sid} unit={state.get('active_unit')} phase={state.get('phase')}")


def scoped_slices(root, scope=""):
    entries = manifests(root)
    state = load(root / "migration.yaml")
    if scope == "--all":
        return list(entries)
    sid = scope or state.get("current_unit")
    if sid not in entries:
        raise WorkflowError("No active slice; reconcile first or pass a slice ID/--all")
    return [sid]


def gate(project, kind, scope=""):
    root = project / ".ctrust"
    if not root.is_dir():
        raise WorkflowError("Migration gates require initialized .ctrust state")
    for sid in scoped_slices(root, scope):
        inventory = root / f"inventory/slices/{sid}"
        require_inventory(root, sid)
        if kind == "inventory":
            pass
        elif kind == "prerequisites":
            prerequisites(root, sid, "port")
        elif kind == "structures":
            require_confirmed(inventory / "data-structures.yaml", STRUCTURE_FIELDS)
            require_mapping(inventory / "mapping-confirmation.yaml")
        elif kind == "behavior":
            require_confirmed(inventory / "behavior.yaml", BEHAVIOR_FIELDS)
        elif kind in {"functions", "comparison", "sources"}:
            _, data = read_map(root, sid)
            for fn in data["functions"]:
                require_non_port_decision(fn)
                if kind != "sources":
                    validate_function(project, sid, fn)
                elif is_non_port_role(fn):
                    continue
                else:
                    source = evidence_path(project, fn.get("rust_file", ""))
                    lines = source.read_text().splitlines()
                    name = fn.get("rust_name") or fn["c_name"]
                    positions = [i for i, line in enumerate(lines) if re.search(r"\bfn\s+" + re.escape(name) + r"\b", line)]
                    if not positions or not any(any("ctrust:source" in line and re.search(r"\bsymbol=" + re.escape(fn["c_name"]) + r"(?=\s|$)", line) and re.search(r"\bslice=" + re.escape(sid) + r"(?=\s|$)", line) for line in lines[max(0, i-4):i]) for i in positions):
                        raise WorkflowError(f"Missing mapped Rust function or C origin comment: {sid}/{name} in {source}")
            if kind == "comparison":
                comparison = load(inventory / "comparison.yaml")
                if comparison.get("status") != "passed":
                    raise WorkflowError(f"Comparison not passed: {sid}")
                recorded = {f["function_id"]: f for f in comparison.get("functions", [])}
                if any(recorded.get(f["function_id"], {}).get("comparison_status") != f.get("comparison_status") or recorded.get(f["function_id"], {}).get("evidence_refs") != f.get("evidence_refs") for f in data["functions"]):
                    raise WorkflowError(f"Comparison artifact is stale: {sid}")
        else:
            raise WorkflowError(f"Unknown gate: {kind}")
    print(f"{kind} gate passed ({scope or 'active slice'})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("project", type=Path)
    parser.add_argument("args", nargs="*")
    args, extra = parser.parse_known_args()
    values = args.args + extra
    try:
        if args.command == "read-state":
            value = load(args.project.resolve() / ".ctrust/migration.yaml")
            for key in values:
                value = value.get(key, "") if isinstance(value, dict) else ""
            print(value)
        elif args.command == "check-confirmed":
            require_confirmed(args.project.resolve() / ".ctrust" / values[0])
        elif args.command == "gate":
            gate(args.project.resolve(), values[0], values[1] if len(values) > 1 else "")
        elif args.command == "scan-files":
            project = args.project.resolve()
            root = project / ".ctrust"
            files = set()
            for sid in scoped_slices(root, values[0] if values else ""):
                _, data = read_map(root, sid)
                for fn in data["functions"]:
                    require_non_port_decision(fn)
                    if is_non_port_role(fn):
                        continue
                    files.add(str(evidence_path(project, fn.get("rust_file", ""))))
            print("\n".join(sorted(files)))
        else:
            operate(args.project.resolve(), args.command, values)
    except (WorkflowError, ValueError, OSError, IndexError, TransactionError) as exc:
        print(f"Workflow blocked: {exc}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
