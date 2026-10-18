#!/usr/bin/env python3
import argparse
from pathlib import Path
from dataclasses import replace

from .core import CtrustOrganizer
from .models import FunctionInput, OrganizationInput, SliceInput
from .serializers import plan_to_dict
from workflow import WorkflowError, load, save, manifests, require_inventory, require_action


def differences(actual, expected, prefix=''):
    if isinstance(actual, dict) and isinstance(expected, dict):
        for key in sorted(actual.keys() | expected.keys(), key=str):
            field = f'{prefix}.{key}' if prefix else str(key)
            if key not in actual:
                yield f'{field}: missing; expected {expected[key]!r}'
            elif key not in expected:
                yield f'{field}: unexpected field'
            else:
                yield from differences(actual[key], expected[key], field)
    elif isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            yield f'{prefix}: expected {len(expected)} items, observed {len(actual)}'
        for index, (left, right) in enumerate(zip(actual, expected)):
            yield from differences(left, right, f'{prefix}[{index}]')
    elif type(actual) is not type(expected) or actual != expected:
        yield f'{prefix}: expected {expected!r}, observed {actual!r}'


def _function_inputs(root, item):
    sid = item['id']
    map_path = root / f'inventory/slices/{sid}/function-map.yaml'
    if map_path.exists():
        data = load(map_path)
        return tuple(
            FunctionInput(
                fn['c_name'],
                fn.get('rust_name', ''),
                fn.get('function_id', ''),
                fn.get('definition_file', ''),
                fn.get('definition_line'),
            )
            for fn in data.get('functions', [])
        )
    return tuple(
        FunctionInput(
            fn['c_name'],
            fn.get('rust_name', ''),
            fn.get('function_id', ''),
            fn.get('definition_file', fn.get('declaration_file', '')),
            fn.get('definition_line', fn.get('declaration_line')),
        )
        for fn in item.get('source', {}).get('functions', [])
    )


def _behavior_test_cases(behavior_data):
    cases = behavior_data.get('test_cases') or behavior_data.get('behavior_cases') or []
    if cases:
        return cases
    aggregated = []
    for function in behavior_data.get('functions', []):
        function_name = function.get('c_name', 'function') if isinstance(function, dict) else 'function'
        for index, case in enumerate(function.get('test_cases', []) if isinstance(function, dict) else [], 1):
            if isinstance(case, str):
                aggregated.append(case)
            elif isinstance(case, dict):
                aggregated.append(case.get('id') or f'{function_name}-case-{index}')
    return aggregated


def build_plan(project, sid=''):
    root = project / '.ctrust'
    state = load(root / 'migration.yaml')
    entries = manifests(root)
    sid = sid or state.get('current_unit')
    if sid not in entries:
        raise WorkflowError('No active slice; reconcile first or pass --slice')
    require_inventory(root, sid)
    selected, manifest, manifest_data = entries[sid]
    target = selected.get('target', {})
    path = target.get('path') or state.get('target', {}).get('path') or state.get('target', {}).get('project_root') or str(project)
    crate_root = Path(path)
    if not crate_root.is_absolute():
        crate_root = project / crate_root
    crate_root = crate_root.resolve()
    kind = target.get('output_kind') or state.get('output', {}).get('kind', 'library')
    if kind not in {'library', 'binary'}:
        raise WorkflowError(f'Invalid output kind: {kind}')
    unit = manifest_data.get('unit', manifest.stem)
    persisted_path = root / f'plans/units/{unit}-symbol-map.yaml'
    persisted_mapping = load(persisted_path) if persisted_path.exists() else None
    # Build one plan from every slice in this crate so shared C symbols receive one identity.
    slices = []
    for item in manifest_data['slices']:
        item_target = item.get('target', {})
        item_path = item_target.get('path') or state.get('target', {}).get('path') or state.get('target', {}).get('project_root') or str(project)
        item_root = Path(item_path)
        item_root = (project / item_root).resolve() if not item_root.is_absolute() else item_root.resolve()
        if item_root != crate_root:
            continue
        behavior = root / f"inventory/slices/{item['id']}/behavior.yaml"
        behavior_data = load(behavior) if behavior.exists() else {}
        cases = item.get('characterization_tests') or _behavior_test_cases(behavior_data)
        cases = tuple(case if isinstance(case, str) else case.get('id', '') for case in cases)
        slices.append(SliceInput(item['id'], objective=item.get('objective') or item.get('goal', ''), functions=_function_inputs(root, item), test_cases=cases, name_override=item.get('semantic_name')))
    plan = CtrustOrganizer().plan(OrganizationInput(kind, str(crate_root), unit, tuple(slices), persisted_mapping=persisted_mapping))
    return replace(plan, slices=tuple(item for item in plan.slices if item.slice_id == sid))


def validate_persisted(project, sid=''):
    plan = build_plan(project, sid)
    errors = CtrustOrganizer().validate(plan)
    if errors:
        raise WorkflowError('; '.join(f'{e.owner}: {e.rule}' for e in errors))
    root = project / '.ctrust'
    sid = sid or load(root / 'migration.yaml').get('current_unit')
    path = root / f'plans/units/{sid}-organization.yaml'
    path = path if path.exists() else root / 'plans/units/organization.yaml'
    actual = load(path)
    expected = plan_to_dict(plan)
    changed = list(differences(actual, expected))
    if changed:
        raise WorkflowError(f'{path}: Organization plan is stale or invalid; regenerate with organize after reviewing input changes: ' + '; '.join(changed))
    return plan


def write_plan(project, sid=''):
    root = project / '.ctrust'
    state = load(root / 'migration.yaml')
    sid = sid or state.get('current_unit')
    require_action(root, state, sid, 'organize')
    plan = build_plan(project, sid)
    errors = CtrustOrganizer().validate(plan)
    if errors:
        raise WorkflowError('; '.join(f'{e.owner}: {e.rule}' for e in errors))
    serialized = plan_to_dict(plan)
    save(root, root / 'plans/units/organization.yaml', serialized)
    save(root, root / f'plans/units/{sid}-organization.yaml', serialized)
    save(root, root / f'plans/units/{plan.unit}-symbol-map.yaml', {'version': 1, 'unit': plan.unit, 'crate_root': plan.crate_root, 'symbols': serialized['symbols']})
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--slice', default='')
    parser.add_argument('--unit', default='')
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    try:
        project = args.project.resolve()
        if args.validate:
            plan = validate_persisted(project, args.slice)
        else:
            plan = build_plan(project, args.slice)
            if args.unit and plan.unit != args.unit:
                raise WorkflowError(f'Unit not found: {args.unit}')
            plan = write_plan(project, args.slice)
            print('organization plan written')
        print(f'organization valid: {len(plan.slices)} slices')
        return 0
    except (WorkflowError, OSError) as exc:
        print(f'Organization blocked: {exc}')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
