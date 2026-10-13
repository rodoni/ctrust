#!/usr/bin/env python3
import argparse
from pathlib import Path
from dataclasses import replace

from .core import CtrustOrganizer
from .models import FunctionInput, OrganizationInput, SliceInput
from .serializers import plan_to_dict
from workflow import WorkflowError, load, save, manifests


def build_plan(project, sid=''):
    root = project / '.ctrust'
    state = load(root / 'migration.yaml')
    entries = manifests(root)
    sid = sid or state.get('current_unit')
    if sid not in entries:
        raise WorkflowError('No active slice; reconcile first or pass --slice')
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
    # A plan covers only slices in this manifest assigned to this crate.
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
        cases = item.get('characterization_tests') or behavior_data.get('test_cases') or []
        cases = tuple(case if isinstance(case, str) else case.get('id', '') for case in cases)
        functions = tuple(FunctionInput(fn['c_name'], fn.get('rust_name', '')) for fn in item.get('source', {}).get('functions', []))
        slices.append(SliceInput(item['id'], objective=item.get('objective', ''), functions=functions, test_cases=cases, name_override=item.get('semantic_name')))
    plan = CtrustOrganizer().plan(OrganizationInput(kind, str(crate_root), manifest_data.get('unit', manifest.stem), tuple(slices)))
    return replace(plan, slices=tuple(item for item in plan.slices if item.slice_id == sid))


def validate_persisted(project, sid=''):
    plan = build_plan(project, sid)
    errors = CtrustOrganizer().validate(plan)
    if errors:
        raise WorkflowError('; '.join(f'{e.owner}: {e.rule}' for e in errors))
    root = project / '.ctrust'
    sid = sid or load(root / 'migration.yaml').get('current_unit')
    path = root / f'plans/units/{sid}-organization.yaml'
    actual = load(path if path.exists() else root / 'plans/units/organization.yaml')
    expected = plan_to_dict(plan)
    if actual != expected:
        raise WorkflowError('Organization plan is stale or invalid; regenerate with organize after reviewing input changes')
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
            errors = CtrustOrganizer().validate(plan)
            if errors:
                raise WorkflowError('; '.join(f'{e.owner}: {e.rule}' for e in errors))
            root = project / '.ctrust'
            save(root, root / 'plans/units/organization.yaml', plan_to_dict(plan))
            sid = args.slice or load(root / 'migration.yaml').get('current_unit')
            save(root, root / f'plans/units/{sid}-organization.yaml', plan_to_dict(plan))
            print('organization plan written')
        print(f'organization valid: {len(plan.slices)} slices')
        return 0
    except (WorkflowError, OSError) as exc:
        print(f'Organization blocked: {exc}')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
