"""Public schema-3 CLI. Bash wrappers only forward arguments."""
import argparse
import copy
import json
import shlex
import sys
from pathlib import Path

import yaml

from .transaction import TransactionError, atomic_write
from .store import Store, MigrationError, read_yaml, digest, local_path
from .inventory import collect
from .core import draft_plan, architecture_errors, report, decide, slice_order, slice_token, slice_verified, dependency_token, dependencies, owned, source_current, workspace_hash
from .execution import prepare, confirm, implementation, evidence, compare, validate_slice, run_gates


def parser():
    cli = argparse.ArgumentParser(description='ctrust v2 — scoped inventory, confirmed architecture, canonical function progress (schema 3)')
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'resume', 'doctor', 'report', 'finish', 'checkpoint'):
        command = commands.add_parser(name)
        command.add_argument('project', type=Path)
        if name in ('status', 'resume', 'doctor'):
            command.add_argument('--json', action='store_true')
    command = commands.add_parser('inventory')
    command.add_argument('project', type=Path)
    command.add_argument('--source', type=Path, required=True)
    command.add_argument('--header', action='append', required=True)
    command.add_argument('--function', action='append')
    command.add_argument('--compile-commands', type=Path)
    command = commands.add_parser('architecture')
    command.add_argument('project', type=Path)
    group = command.add_mutually_exclusive_group(required=True)
    group.add_argument('--export', type=Path)
    group.add_argument('--plan', type=Path)
    command.add_argument('--confirm', action='store_true')
    command = commands.add_parser('prepare')
    command.add_argument('project', type=Path)
    command.add_argument('slice')
    command = commands.add_parser('confirm')
    command.add_argument('project', type=Path)
    command.add_argument('slice')
    command.add_argument('stage', choices=('structures', 'behavior', 'contract', 'model', 'organization'))
    command.add_argument('artifact')
    for name in ('record-implementation', 'record-evidence', 'compare'):
        command = commands.add_parser(name)
        command.add_argument('project', type=Path)
        command.add_argument('function')
        if name != 'compare':
            command.add_argument('path')
        if name == 'record-implementation':
            command.add_argument('module')
    for name in ('verify-slice', 'gate'):
        command = commands.add_parser(name)
        command.add_argument('project', type=Path)
        if name == 'gate':
            command.add_argument('slice', nargs='?', default='all')
        else:
            command.add_argument('slice')
    return cli


def next_command(decision):
    action = decision['action']
    args = ['.ctrust/sync.sh', action, '.']
    if action == 'completed':
        return ''
    if action == 'confirm':
        args += [decision['slice'], decision['stage'], f'.ctrust/artifacts/{decision["slice"]}/{decision["stage"]}.yaml']
    elif action == 'verify-slice':
        args += [decision['slice']]
    elif 'function' in decision:
        args += [decision['function']]
        if action == 'record-implementation':
            return shlex.join(args) + ' auto <module definido em architecture>'
        if action == 'record-evidence':
            return shlex.join(args) + ' <arquivo.ndjson>'
    elif action in ('architecture', 'inventory'):
        return ''  # Missing decisions/paths are an analysis task, never invented.
    return shlex.join(args)


def show(project, state, structured=False):
    value = report(project, state)
    value['next']['command'] = next_command(value['next'])
    if value['next'].get('function') and value['next']['action'] == 'record-implementation':
        from .execution import rust_target
        _, module, _ = rust_target(project, state, value['next']['function'])
        value['next']['command'] = shlex.join(['.ctrust/sync.sh', 'record-implementation', '.', value['next']['function'], 'auto', module])
    if value['next']['action'] == 'record-evidence':
        fid = value['next']['function']
        path = state['progress'].get(fid, {}).get('evidence', {}).get('path') or f'.ctrust/evidence/{digest(fid)[:16]}.ndjson'
        value['next']['command'] = shlex.join(['.ctrust/sync.sh', 'record-evidence', '.', fid, path])
        value['next']['artifact'] = path
        value['next']['reason'] = 'Execute tests, write current evidence to artifact, then record it'
    if value['next']['action'] == 'confirm':
        sid, stage = value['next']['slice'], value['next']['stage']
        artifact = state.get('preparation', {}).get(sid, {}).get(stage, {}).get('path') or f'.ctrust/artifacts/{sid}/{stage}.yaml'
        value['next'].update(artifact=artifact, command=shlex.join(['.ctrust/sync.sh', 'confirm', '.', sid, stage, artifact]))
    if structured:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        totals = value['totals']
        print(f'ctrust schema=3 revision={state["revision"]}')
        print(f'Functions: {totals["selected"]} selected; {totals["implemented"]} implemented; {totals["tested"]} tested; {totals["verified"]} verified (cumulative milestones)')
        print(f'Verification: {totals["parity_verified"]} executable-reference parity; {totals["verified_without_legacy_reference"]} behavior-contract only')
        print(f'Last operation: {value["last_operation"] or "none"}')
        for row in value['slices']:
            print(f'Slice {row["slice"]}: {row["verified"]}/{row["selected"]} functions verified; {len(row["references"])} reused references; slice verified={row["verified_slice"]}; Cargo inputs current={row["cargo_inputs_current"]}')
        for issue in value['issues'].values():
            print(f'ISSUE scope={issue["scope"]} operation={issue["operation"]}: {issue["reason"]}')
        for scope, run in value['gates'].items():
            for gate in run['gates']:
                print(f'GATE scope={scope}/{gate["scope"]} name={gate["gate"]} result={gate["result"]} log={gate.get("log", "")}' )
        print('NEXT: ' + json.dumps(value['next'], ensure_ascii=False))
    return value


def checkpoint(store, state):
    content = '# ctrust checkpoint\n\n```json\n' + json.dumps(report(store.project, state), indent=2, ensure_ascii=False) + '\n```\n'
    atomic_write(store.root, f'checkpoints/revision-{state["revision"]:06d}.md', content.encode())


def active_owner(project, state, fid):
    if not state['architecture'] or fid not in state['architecture']['plan']['assignments']:
        raise MigrationError(f'Unknown function: {fid}')
    owner = state['architecture']['plan']['assignments'][fid].get('slice')
    if state['architecture']['plan']['assignments'][fid]['role'] != 'port':
        raise MigrationError(f'{fid}: function is not selected for port')
    if not source_current(state):
        raise MigrationError('Source changed; refresh inventory first')
    for dep in dependencies(state)[owner]:
        if not slice_verified(project, state, dep):
            raise MigrationError(f'{fid}: dependency {dep} must be verified before work on {owner}')
    current = decide(project, state)
    if current.get('slice') and current['slice'] != owner:
        # Explicit corrections to already-owned work are allowed; do not start
        # an unrelated future slice while the current one remains incomplete.
        order = slice_order(state)
        if order.index(owner) > order.index(current['slice']):
            raise MigrationError(f'Complete active slice {current["slice"]} before {owner}')


def execute(args, store, state):
    project = store.project
    command = args.command
    if command == 'inventory':
        root = args.source.resolve()
        inventory = collect(root, args.header, args.function, args.compile_commands, store.root / 'cache' / 'analyzer')
        source = {'root': str(root), 'headers': args.header, 'functions': args.function, 'compile_commands': str(args.compile_commands.resolve()) if args.compile_commands else None}
        if state['inventory'] != inventory or state['source'] != source:
            state['source'] = source
            state['inventory'] = inventory
            # Old approvals remain in history, never silently adopted under new input.
            state['architecture'] = {}
    elif command == 'architecture':
        if args.export:
            plan = state['architecture'].get('plan') or draft_plan(state)
            args.export.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding='utf-8')
            print(f'Architecture exported: {args.export}')
            return
        plan = read_yaml(args.plan)
        errors = architecture_errors(state, plan)
        if errors:
            raise MigrationError('; '.join(errors))
        print('Architecture valid; order: ' + ', '.join(slice_order(state, plan)))
        if args.confirm:
            if not source_current(state):
                raise MigrationError('Source changed since inventory')
            state['architecture'] = {'plan': plan, 'fingerprint': digest(plan)}
    elif command == 'prepare':
        print('Artifacts: ' + str(prepare(project, state, args.slice)))
    elif command == 'confirm':
        # Preparation cannot bypass dependency readiness or active slice order.
        if not state['architecture']:
            raise MigrationError('Confirm architecture first')
        for fid in owned(state, args.slice):
            active_owner(project, state, fid)
            break
        confirm(project, state, args.slice, args.stage, args.artifact)
    elif command in ('record-implementation', 'record-evidence', 'compare'):
        active_owner(project, state, args.function)
        if command == 'record-implementation':
            implementation(project, state, args.function, args.path, args.module)
        elif command == 'record-evidence':
            evidence(project, state, args.function, args.path)
        else:
            compare(project, state, args.function)
    elif command in ('verify-slice', 'finish', 'gate'):
        if not state['architecture']:
            raise MigrationError('Confirm architecture first')
        slices = [args.slice] if command == 'verify-slice' or (command == 'gate' and args.slice != 'all') else slice_order(state)
        for sid in slices:
            validate_slice(project, state, sid)
        if command == 'finish' and not all(slice_verified(project, state, sid) for sid in slices):
            raise MigrationError('Verify all slices before finish')
        if command == 'verify-slice' and slice_verified(project, state, args.slice):
            print(f'{args.slice}: already verified; checkpoint can be regenerated')
            return
        if command == 'finish' and decide(project, state)['action'] == 'completed':
            print('Migration already completed')
            return
        before = {sid: slice_token(project, state, sid) for sid in slices}
        result = run_gates(project, state, getattr(args, 'slice', 'final'))
        if any(slice_token(project, state, sid) != before[sid] for sid in slices):
            result.update(result='failed', inputs_unchanged=False)
        state.setdefault('gate_runs', {})[getattr(args, 'slice', 'final')] = result
        if result['result'] != 'passed':
            # Publish the failure once without promoting anything. The CLI
            # reports nonzero after commit; prior canonical progress survives.
            state['issues'][issue_key(args)] = {'code': 'gate_failed' if result['inputs_unchanged'] else 'inputs_changed', 'operation': command, 'scope': getattr(args, 'slice', 'workspace'), 'reason': 'Gate failed or inputs changed; inspect gate results/logs, correct cause and repeat', 'input': digest(before)}
            return
        if command == 'finish':
            state['closure'] = {'workspace': result['workspace'], 'slices': digest(state['slice_results'])}
        elif command == 'verify-slice':
            sid = args.slice
            state['slice_results'][sid] = {'token': before[sid], 'dependency_token': dependency_token(project, state, sid), 'result': 'passed', 'workspace': result.get('workspace'), 'dependencies': {d: state['slice_results'][d]['dependency_token'] for d in dependencies(state)[sid]}, 'gates': result['gates']}
    else:
        raise MigrationError(f'Unknown operation: {command}')


def issue_key(args):
    return ':'.join((args.command, issue_scope(args), getattr(args, 'stage', '')))


def issue_scope(args):
    function = getattr(args, 'function', None)
    return function if isinstance(function, str) else getattr(args, 'slice', 'workspace')


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.project)
    try:
        if args.command == 'init':
            show(store.project, store.initialize())
            return 0
        if args.command in ('status', 'resume', 'doctor', 'report', 'checkpoint'):
            state = store.read()
            if args.command == 'checkpoint':
                checkpoint(store, state)
            elif args.command == 'report':
                from .html import write_report
                print(write_report(store.project, state))
            else:
                value = show(store.project, state, getattr(args, 'json', False))
                if args.command == 'doctor' and value['next']['action'] in ('inventory', 'architecture'):
                    return 3
            return 0
        print(f'ACTION START {args.command}', flush=True)
        with store.change() as state:
            before = digest(state)
            previous = copy.deepcopy({key: value for key, value in state.items() if key not in ('history', 'revision')})
            state['issues'].pop(issue_key(args), None)
            execute(args, store, state)
            if digest(state) != before:
                changed = {}
                for key, old in previous.items():
                    new = state.get(key)
                    if old == new:
                        continue
                    changed[key] = ({name: old.get(name) for name in old.keys() | new.keys() if old.get(name) != new.get(name)} if isinstance(old, dict) and isinstance(new, dict) else old)
                state['history'].append({'operation': args.command, 'result': 'failed' if issue_key(args) in state['issues'] else 'passed', 'revision': state['revision'] + 1, 'input': before, 'previous': changed})
        state = store.read()
        if args.command in ('verify-slice', 'finish') and issue_key(args) not in state['issues']:
            checkpoint(store, state)
        show(store.project, state)
        failed = issue_key(args) in state['issues']
        print(f'ACTION END {args.command} result={"failed" if failed else "passed"}', flush=True)
        return 3 if failed else 0
    except (MigrationError, TransactionError, OSError, ValueError, KeyError, TypeError) as exc:
        # A rejected operation cannot publish its partial data. Record only a
        # diagnostic in a new transaction; never count a second execution.
        if args.command not in ('status', 'resume', 'doctor', 'report', 'checkpoint'):
            try:
                with store.change() as state:
                    issue = {'code': 'execution_error' if isinstance(exc, OSError) else 'validation_rejected', 'operation': args.command, 'scope': issue_scope(args), 'reason': str(exc)}
                    if state['issues'].get(issue_key(args)) != issue:
                        state['issues'][issue_key(args)] = issue
                        state['history'].append({'operation': args.command, 'result': 'rejected', 'revision': state['revision'] + 1, 'issue': issue})
            except (MigrationError, TransactionError, OSError):
                pass
        print(f'ACTION END {args.command} result=failed: {exc}', file=sys.stderr)
        if args.command not in ('status', 'resume', 'doctor', 'report', 'checkpoint'):
            try:
                show(store.project, store.read())
            except (MigrationError, OSError, KeyError, TypeError, ValueError):
                pass
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
