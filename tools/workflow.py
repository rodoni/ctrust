"""Structured artifact handling shared by the controller and migration gates."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from artifact_sync.transaction import WorkspaceLock, atomic_write, TransactionError
from function_recovery import UnitState, ALLOWED_TRANSITIONS


class WorkflowError(ValueError):
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


def confirmed(path, fields=()):
    if not path.is_file():
        return False
    data = load(path)
    return data.get("status") == "confirmed" and all(data.get("completion", {}).get(f) is True for f in fields)


def manifests(root):
    entries = {}
    for path in sorted((root / "plans/units").glob("*-slices.yaml")):
        data = load(path)
        for item in data.get("slices", []):
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
        raise WorkflowError(f"Evidence/destination not found: {path}")
    return path


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


def prerequisites(root, sid, stage):
    inventory = root / f"inventory/slices/{sid}"
    if not confirmed(inventory / "data-structures.yaml", STRUCTURE_FIELDS):
        raise WorkflowError(f"Data structures not fully confirmed: {sid}")
    mapping = load(inventory / "mapping-confirmation.yaml")
    if mapping.get("status") != "confirmed" or mapping.get("counts", {}).get("unresolved") != 0:
        raise WorkflowError(f"Mapping unresolved or unconfirmed: {sid}")
    if not confirmed(inventory / "behavior.yaml", BEHAVIOR_FIELDS):
        raise WorkflowError(f"Behavior not fully confirmed: {sid}")
    if stage in {"model", "port"} and not confirmed(root / f"contracts/units/{sid}.yaml"):
        raise WorkflowError(f"Contract not confirmed: {sid}")
    if stage == "port":
        if not confirmed(root / f"plans/units/{sid}-model.yaml"):
            raise WorkflowError(f"Model not confirmed: {sid}")
        from organization.cli import validate_persisted
        validate_persisted(root.parent, sid)


def reconcile(project, root, state):
    entries = manifests(root)
    if state.get("status") == "blocked" or state.get("attempts", 0) >= state.get("max_attempts", 2):
        print("reconcile: preserved blocker and retry guard; resolve cause and use next explicitly")
        return
    sid = select_slice(entries, state)
    if not sid:
        state.update(status="in_progress", phase="verification", next_action="Review final evidence and checkpoint before closing the workspace", next_skill="migration-verification", next_command="", active_unit="none", active_functions=[], active_function_ids=[], next_function="none", active_rust_file="", active_module_path="", active_evidence="")
    else:
        inventory = root / f"inventory/slices/{sid}"
        phase, skill, command = "inventory", "c-code-inventory", ""
        action = f"Complete inventory for {sid}"
        if entries[sid][0].get("status") == "blocked":
            phase, skill, action = "blocked", "context-recovery", f"Resolve recorded blocker for {sid}"
        elif inventory.is_dir() and any(inventory.iterdir()):
            try:
                prerequisites(root, sid, "contract")
                ready = True
            except WorkflowError:
                ready = False
            if not confirmed(inventory / "data-structures.yaml", STRUCTURE_FIELDS) or not (inventory / "mapping-confirmation.yaml").is_file() or load(inventory / "mapping-confirmation.yaml").get("status") != "confirmed" or load(inventory / "mapping-confirmation.yaml").get("counts", {}).get("unresolved") != 0:
                phase, skill, action = "data_structure_mapping", "data-structure-mapping", f"Confirm structures and mapping for {sid}"
            elif not ready:
                phase, skill, action = "behavior_extraction", "c-behavior-extraction", f"Confirm behavior for {sid}"
            elif not confirmed(root / f"contracts/units/{sid}.yaml"):
                phase, skill, action = "contract_analysis", "contract-extraction", f"Complete and confirm contract for {sid}"
                if not (root / f"contracts/units/{sid}.yaml").exists():
                    command = f".ctrust/sync.sh contract . {sid}"
            elif not confirmed(root / f"plans/units/{sid}-model.yaml"):
                phase, skill, action = "modeling", "rust-domain-modeling", f"Complete and confirm model for {sid}"
                if not (root / f"plans/units/{sid}-model.yaml").exists():
                    command = f".ctrust/sync.sh model . {sid}"
            else:
                try:
                    from organization.cli import validate_persisted
                    validate_persisted(project, sid)
                    organization_ready = True
                except (WorkflowError, ValueError):
                    organization_ready = False
                if not organization_ready:
                    phase, skill, action = "organization", "rust-module-organization", f"Define and validate organization for {sid}"
                    if not (root / f"plans/units/{sid}-organization.yaml").exists():
                        command = ".ctrust/sync.sh organize ."
                elif not (inventory / "function-map.yaml").exists():
                    phase, skill, action = "inventory", "c-code-inventory", f"Create missing function map for {sid}"
                else:
                    _, data = read_map(root, sid, upgrade=True)
                    if all(f["state"] == "verified" for f in data["functions"]):
                        phase, skill, action = "verification", "migration-verification", f"Run slice gates and checkpoint {sid}"
                    else:
                        phase, skill, action = "function_recovery", "c-to-rust-migration", f"Resume next incomplete function in {sid}"
                        command = f".ctrust/sync.sh select-function . {sid}"
        if state.get("current_unit") != sid:
            state.update(active_unit="none", active_functions=[], active_function_ids=[], next_function="none", active_rust_file="", active_module_path="", active_evidence="", implementation_status="pending", test_status="pending", comparison_status="pending")
        state.update(current_unit=sid, status="blocked" if phase == "blocked" else "in_progress", phase=phase, next_action=action, next_skill=skill, next_command=command)
    state["next_owner"] = "ctrust-lead"
    save(root, root / "migration.yaml", state)
    print(f"reconcile: slice={sid or 'none'} phase={state['phase']}")


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
            if state.get("status") == "blocked":
                raise WorkflowError("Resolve the blocker before verifying the slice")
            entries = manifests(root)
            if select_slice(entries, state) != sid:
                raise WorkflowError("Only the active eligible slice can be verified")
            prerequisites(root, sid, "port")
            for kind in ("structures", "behavior", "functions", "comparison", "sources"):
                gate(project, kind, sid)
            item, path, data = entries[sid]
            item["status"] = "verified"
            save(root, path, data)
            state.update(status="in_progress", phase="verification", next_command="", next_action=f"Checkpoint verified slice {sid}, then reconcile", next_skill="context-recovery")
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
            save(root, path, data)
            print(f"function-map valid: slice={sid}, functions={len(data['functions'])}")
            return
        if state.get("status") == "blocked" or state.get("attempts", 0) >= state.get("max_attempts", 2):
            raise WorkflowError("Migration blocked; resolve the cause and record a different next action")
        if select_slice(entries, state) != sid:
            raise WorkflowError(f"Slice {sid} is not eligible or another slice is active")
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
                destination = evidence_path(project, args[1])
                from organization.cli import validate_persisted
                plan = validate_persisted(project, sid)
                planned = plan.slices[0]
                expected_file = (Path(plan.crate_root) / planned.implementation_file).resolve()
                if destination != expected_file or args[2] != planned.module_path:
                    raise WorkflowError(f"Implementation must follow organization plan: {expected_file}, {planned.module_path}")
                set_transition(fn, "implemented")
                fn.update(rust_file=str(destination.relative_to(project)), module_path=args[2], implementation_status="implemented")
                state.update(implementation_status="implemented", active_rust_file=fn["rust_file"], active_module_path=args[2], phase="function_testing", next_action=f"Run tests and record evidence for {args[0]}", next_skill="migration-verification", next_command="")
            elif command == "record-evidence":
                evidence_records(project, args[1], sid, args[0], fn["function_id"])
                set_transition(fn, "tested")
                fn.setdefault("evidence_refs", []).append(str(evidence_path(project, args[1]).relative_to(project)))
                fn["test_status"] = "passed"
                state.update(test_status="passed", active_evidence=args[1], phase="function_comparison", next_action=f"Compare and classify evidence for {args[0]}", next_skill="migration-verification", next_command="")
            elif command == "compare":
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
                        state.update(phase="verification", next_skill="migration-verification", next_action=f"Run migration-gate for {sid}, then checkpoint and mark slice verified")
        data["revision"] += 1
        save(root, path, data)
        state["function_map_revision"] = data["revision"]
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
        if kind == "prerequisites":
            prerequisites(root, sid, "port")
        elif kind == "structures":
            if not confirmed(inventory / "data-structures.yaml", STRUCTURE_FIELDS):
                raise WorkflowError(f"Data structures not fully confirmed: {sid}")
            mapping = load(inventory / "mapping-confirmation.yaml")
            if mapping.get("status") != "confirmed" or mapping.get("counts", {}).get("unresolved") != 0:
                raise WorkflowError(f"Mapping unresolved or unconfirmed: {sid}")
        elif kind == "behavior":
            if not confirmed(inventory / "behavior.yaml", BEHAVIOR_FIELDS):
                raise WorkflowError(f"Behavior not fully confirmed: {sid}")
        elif kind in {"functions", "comparison", "sources"}:
            _, data = read_map(root, sid)
            for fn in data["functions"]:
                if kind != "sources":
                    validate_function(project, sid, fn)
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
        if args.command == "gate":
            gate(args.project.resolve(), values[0], values[1] if len(values) > 1 else "")
        elif args.command == "scan-files":
            project = args.project.resolve()
            root = project / ".ctrust"
            files = set()
            for sid in scoped_slices(root, values[0] if values else ""):
                _, data = read_map(root, sid)
                for fn in data["functions"]:
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
