"""Pure architecture validation and one decision function for execution/reporting."""
from pathlib import Path
import os
import heapq
import re

from .store import MigrationError, digest, file_hash, local_path

STAGES = ('structures', 'behavior', 'contract', 'model', 'organization')
ROLES = {'port', 'adapt', 'simulate', 'exclude'}
RUST_KEYWORDS = set('_ as async await break const continue crate dyn else enum extern false fn for gen if impl in let loop match mod move mut pub ref return self static struct super trait true type unsafe use where while abstract become box do final macro override priv typeof unsized virtual yield try'.split())


def rust_name(name):
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name).lower()
    return 'c_' + name if name in RUST_KEYWORDS else name


def draft_plan(state):
    inventory = state['inventory']
    if not inventory:
        raise MigrationError('Inventory is required before architecture')
    return {'version': 3, 'inventory': inventory['fingerprint'], 'slices': {}, 'assignments': {
        fid: {'role': 'port' if fn.get('status') == 'resolved' else 'adapt', 'slice': '', 'reason': '', 'rust_name': rust_name(fn['label'])}
        for fid, fn in inventory['functions'].items()
    }}


def shared_candidates(state):
    inventory = state['inventory']
    consumers = {}
    for root in inventory.get('roots', []):
        queue, visited = [root], set()
        while queue:
            fid = queue.pop()
            if fid in visited:
                continue
            visited.add(fid)
            consumers.setdefault(fid, set()).add(root)
            queue.extend(inventory['functions'][fid].get('calls', []))
    return [{'function': fid, 'roots': sorted(roots), 'suggestion': 'Review cohesive shared slice; ownership remains a user decision'} for fid, roots in sorted(consumers.items()) if len(roots) > 1]


def architecture_errors(state, plan):
    errors = []
    inventory = state['inventory']
    if plan.get('version') != 3 or plan.get('inventory') != inventory.get('fingerprint'):
        errors.append('Plan must use schema 3 and the current inventory fingerprint')
    slices, assignments = plan.get('slices'), plan.get('assignments')
    if not isinstance(slices, dict) or not slices or not isinstance(assignments, dict):
        return errors + ['Plan requires nonempty slices and assignments mappings']
    include_decisions = plan.get('include_decisions', {})
    if not isinstance(include_decisions, dict):
        errors.append('include_decisions must be a mapping')
        include_decisions = {}
    for item in inventory.get('unresolved_includes', []):
        if item['include'].startswith('<'):
            continue  # System headers remain visible as uncertainty for type review.
        key = item['file'] + '::' + item['include']
        decision = include_decisions.get(key, {})
        if not isinstance(decision, dict) or decision.get('role') not in ('adapt', 'simulate', 'exclude') or not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
            errors.append(f'Missing project header {key}; refresh inventory with --include-dir for additional headers or --dependency-root for external C modules. For duplicate header names, supply --compile-commands with the build include paths. Alternatively record include_decisions role/reason for an intentionally unavailable dependency')
    functions = inventory['functions']
    if set(assignments) != set(functions):
        errors.append('Assignments must classify every inventory function exactly once')
    rust_paths = set()
    modules = set()
    for sid, item in slices.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]*', sid) or not isinstance(item, dict):
            errors.append(f'Invalid slice: {sid}')
            continue
        crate = item.get('crate', '')
        module = item.get('module', '')
        if not isinstance(crate, str) or not crate or Path(crate).is_absolute() or '..' in Path(crate).parts:
            errors.append(f'{sid}: crate must be a relative project path')
        if not isinstance(module, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', module) or module in RUST_KEYWORDS:
            errors.append(f'{sid}: module must be a Rust identifier')
        if isinstance(crate, str) and isinstance(module, str):
            destination = (str(Path(crate)), module)
            if destination in modules:
                errors.append(f'{sid}: module file is already owned by another slice; regroup functions')
            modules.add(destination)
    for fid, assignment in assignments.items():
        if fid not in functions or not isinstance(assignment, dict):
            errors.append(f'Invalid assignment: {fid}')
            continue
        role = assignment.get('role')
        sid = assignment.get('slice')
        if not isinstance(role, str) or role not in ROLES:
            errors.append(f'{fid}: decision must be port/adapt/simulate/exclude')
        elif role == 'port':
            if functions[fid].get('status') != 'resolved':
                errors.append(f'{fid}: unresolved/ambiguous function cannot be ported')
            if not isinstance(sid, str) or sid not in slices:
                errors.append(f'{fid}: exactly one existing owner slice is required')
            elif isinstance(slices[sid], dict):
                name = assignment.get('rust_name', '')
                if not isinstance(name, str) or not re.fullmatch(r'[a-z_][a-z0-9_]*', name) or name in RUST_KEYWORDS:
                    errors.append(f'{fid}: valid rust_name is required')
                if all(isinstance(value, str) for value in (slices[sid].get('crate'), slices[sid].get('module'), name)):
                    key = (slices[sid]['crate'], slices[sid]['module'], name)
                    if key in rust_paths:
                        errors.append(f'{fid}: duplicate Rust symbol destination')
                    rust_paths.add(key)
            for target in functions[fid].get('calls', []):
                decision = assignments.get(target, {})
                if decision.get('role') == 'exclude':
                    errors.append(f'{fid}: required dependency {target} is excluded; use an explicit adapter/simulation')
            # An indirect edge is not a proven call graph. Demand a reviewed
            # decision in the architecture rather than silently accepting it.
            decision = assignment.get('indirect_decision')
            if functions[fid].get('indirect_calls') and (not isinstance(decision, str) or not decision.strip()):
                errors.append(f'{fid}: indirect calls require indirect_decision and reviewed target treatment')
        elif not isinstance(assignment.get('reason'), str) or not assignment['reason'].strip():
            errors.append(f'{fid}: {role} requires a reason')
    for sid in slices:
        if not any(a.get('role') == 'port' and a.get('slice') == sid for a in assignments.values() if isinstance(a, dict)):
            errors.append(f'{sid}: slice has no owned functions')
    if not errors:
        try:
            slice_order(state, plan)
        except MigrationError as exc:
            errors.append(str(exc))
    return errors


def dependencies(state, plan=None):
    plan = plan or state['architecture']['plan']
    result = {sid: set() for sid in plan['slices']}
    for fid, assignment in plan['assignments'].items():
        if assignment['role'] != 'port':
            continue
        owner = assignment['slice']
        for child in state['inventory']['functions'][fid].get('calls', []):
            other = plan['assignments'][child]
            if other['role'] == 'port' and other['slice'] != owner:
                result[owner].add(other['slice'])
    return result


def slice_order(state, plan=None):
    deps = dependencies(state, plan)
    order, dependents = [], {sid: [] for sid in deps}
    for sid, required in deps.items():
        for dependency in required:
            dependents[dependency].append(sid)
    ready = [sid for sid, required in deps.items() if not required]
    heapq.heapify(ready)
    while ready:
        sid = heapq.heappop(ready)
        order.append(sid)
        for consumer in dependents[sid]:
            deps[consumer].remove(sid)
            if not deps[consumer]:
                heapq.heappush(ready, consumer)
    if len(order) != len(deps):
        raise MigrationError('Slice dependency cycle involving ' + ', '.join(sorted(set(deps) - set(order))) + '; regroup functions')
    return order


def owned(state, sid):
    return sorted(fid for fid, a in state['architecture']['plan']['assignments'].items() if a['role'] == 'port' and a['slice'] == sid)


def source_current(state):
    root = Path(state['source']['root'])
    inventory = state['inventory']
    from .inventory import source_snapshot
    current = source_snapshot(root, inventory.get('include_dirs', []), inventory.get('dependency_roots', []))
    database = inventory.get('compile_commands')
    return current == inventory['files'] and (not database or file_hash(database['path']) == database['hash'])


def preparation_token(project, state, sid, until=None):
    token = digest({'architecture': state['architecture']['fingerprint'], 'slice': sid,
                    'dependencies': {dep: state['slice_results'].get(dep, {}).get('dependency_token') for dep in sorted(dependencies(state)[sid])}})
    for stage in STAGES:
        if stage == until:
            return token, None
        record = state.get('preparation', {}).get(sid, {}).get(stage)
        if not record or record['input'] != token or file_hash(local_path(project, record['path'])) != record['hash']:
            return token, stage
        token = digest(record)
    return token, None


def implementation_current(project, state, fid, token):
    record = state['progress'].get(fid, {}).get('implementation')
    return bool(record and record['input'] == token and file_hash(local_path(project, record['path'])) == record['hash'])


def function_step(project, state, fid, token):
    record = state['progress'].get(fid, {})
    if not implementation_current(project, state, fid, token):
        return 'record-implementation'
    evidence = record.get('evidence')
    if not evidence or evidence['implementation'] != digest(record['implementation']) or file_hash(local_path(project, evidence['path'])) != evidence['hash']:
        return 'record-evidence'
    comparison = record.get('comparison')
    if not comparison or comparison.get('evidence') != digest(evidence) or (comparison.get('classification'), comparison.get('scope')) not in {('parity_verified', 'legacy_parity'), ('verified_without_legacy_reference', 'behavior_contract')}:
        return 'compare'
    return None


def slice_token(project, state, sid):
    token, pending = preparation_token(project, state, sid)
    if pending:
        return None
    if any(function_step(project, state, fid, token) for fid in owned(state, sid)):
        return None
    return digest({'preparation': token, 'progress': {fid: state['progress'][fid] for fid in owned(state, sid)}})


def dependency_token(project, state, sid):
    """Consumers bind to reviewed code/contracts, not to evidence formatting."""
    token, pending = preparation_token(project, state, sid)
    if pending:
        return None
    return digest({'preparation': token, 'implementations': {fid: state['progress'].get(fid, {}).get('implementation') for fid in owned(state, sid)}})


def workspace_files(project):
    """Conservative input set for Cargo: ignore generated/toolkit/VCS files."""
    excluded = {'.ctrust', '.git', 'target', '.venv', '__pycache__'}
    files = {}
    for directory, directories, names in os.walk(project):
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_file():
                files[str(path.relative_to(project))] = file_hash(path)
    return files


def workspace_hash(project):
    return digest(workspace_files(project))


def slice_validity(project, state):
    deps, valid = dependencies(state), {}
    for sid in slice_order(state):
        token = slice_token(project, state, sid)
        record = state['slice_results'].get(sid, {})
        expected = {d: state['slice_results'].get(d, {}).get('dependency_token') for d in deps[sid]}
        valid[sid] = bool(token and record.get('token') == token and record.get('result') == 'passed' and record.get('dependencies') == expected and all(valid[dep] for dep in deps[sid]))
    return valid


def slice_verified(project, state, sid):
    # Workspace growth does not erase prior functional results. Final review
    # always reruns Cargo for the current complete workspace.
    return slice_validity(project, state).get(sid, False)


def decide(project, state):
    """The only next-action calculation; no writes, no counters, no transitions."""
    def action(command, **details):
        return {'action': command, **details}
    if not state['inventory']:
        return action('inventory', reason='Define source root and entry headers')
    if not source_current(state):
        return action('inventory', reason='C source or compilation database changed; review inventory')
    if not state['architecture']:
        return action('architecture', reason='Select functions, assign owner slices and confirm plan')
    errors = architecture_errors(state, state['architecture']['plan'])
    if errors:
        return action('architecture', reason='; '.join(errors))
    valid = slice_validity(project, state)
    for sid in slice_order(state):
        if valid[sid]:
            continue
        token, stage = preparation_token(project, state, sid)
        if stage:
            return action('confirm', slice=sid, stage=stage, reason=f'Review and confirm {stage} artifact')
        # A slice must converge to an implemented file set before testing.
        # Otherwise appending another function to the same Rust module would
        # repeatedly invalidate tests of its predecessors or fail Cargo because
        # their callees are still absent. New functions precede stale file stamps.
        functions = sorted(owned(state, sid), key=lambda fid: (bool(state['progress'].get(fid, {}).get('implementation')), fid))
        for fid in functions:
            if function_step(project, state, fid, token) == 'record-implementation':
                return action('record-implementation', slice=sid, function=fid, reason='Complete owned implementations before testing this slice')
        for fid in functions:
            step = function_step(project, state, fid, token)
            if step:
                return action(step, slice=sid, function=fid, reason=f'Complete {step} for owned function')
        return action('verify-slice', slice=sid, reason='Verify gates for slice and publish checkpoint')
    closed = state.get('closure')
    if closed and closed['workspace'] == workspace_hash(project) and closed['slices'] == digest(state['slice_results']):
        return action('completed', reason='Final workspace review passed')
    return action('finish', reason='All slices verified; run final workspace review')


def report(project, state):
    next_action = decide(project, state)
    current = bool(state['inventory']) and source_current(state)
    totals = {'selected': 0, 'implemented': 0, 'tested': 0, 'verified': 0, 'parity_verified': 0, 'verified_without_legacy_reference': 0}
    rows = []
    if state['architecture']:
        deps = dependencies(state)
        valid = slice_validity(project, state)
        workspace = workspace_hash(project) if state['slice_results'] else None
        for sid in slice_order(state):
            token, pending = preparation_token(project, state, sid)
            counts = dict.fromkeys(totals, 0)
            for fid in owned(state, sid):
                step = function_step(project, state, fid, token) if not pending and current else 'record-implementation'
                counts['selected'] += 1
                counts['implemented'] += step in ('record-evidence', 'compare', None)
                counts['tested'] += step in ('compare', None)
                counts['verified'] += step is None
                if step is None:
                    counts[state['progress'][fid]['comparison']['classification']] += 1
            for key in totals:
                totals[key] += counts[key]
            references = {child for fid in owned(state, sid) for child in state['inventory']['functions'][fid].get('calls', []) if state['architecture']['plan']['assignments'][child]['role'] == 'port' and state['architecture']['plan']['assignments'][child]['slice'] != sid}
            rows.append({'slice': sid, **counts, 'references': sorted(references), 'dependencies': sorted(deps[sid]), 'verified_slice': current and valid[sid], 'cargo_inputs_current': bool(workspace and state['slice_results'].get(sid, {}).get('workspace') == workspace), 'pending_stage': pending})
    return {'version': 3, 'revision': state['revision'], 'inputs_current': current, 'unresolved_includes': state['inventory'].get('unresolved_includes', []), 'totals': totals, 'slices': rows, 'shared_candidates': shared_candidates(state), 'next': next_action, 'issues': state.get('issues', {}), 'gates': state.get('gate_runs', {}), 'last_operation': state['history'][-1]['operation'] if state.get('history') else None, 'completed': next_action['action'] == 'completed'}
