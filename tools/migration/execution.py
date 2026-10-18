"""Validated records and gates; never encode a failure as lost progress."""
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from .core import STAGES, owned, dependencies, preparation_token, function_step, slice_token, slice_verified, workspace_hash, workspace_files, source_current
from .store import MigrationError, digest, file_hash, local_path, read_yaml
from .inventory import scoped_structures, scoped_behavior

STRUCTURES = ('structures_identified', 'fields_mapped', 'layout_mapped', 'pointer_semantics_mapped', 'shared_memory_classified', 'rust_strategy_defined', 'evidence_recorded')
BEHAVIOR = ('source_logic_read', 'branches_mapped', 'calls_mapped', 'errors_mapped', 'side_effects_mapped', 'test_cases_defined')


def ensure_slice(state, sid):
    if not state['architecture'] or sid not in state['architecture']['plan']['slices']:
        raise MigrationError(f'Unknown slice: {sid}')


def rust_target(project, state, fid):
    assignment = state['architecture']['plan']['assignments'].get(fid)
    if not assignment or assignment['role'] != 'port':
        raise MigrationError(f'Function is not owned port work: {fid}')
    item = state['architecture']['plan']['slices'][assignment['slice']]
    return local_path(project, str(Path(item['crate']) / 'src' / (item['module'] + '.rs'))), 'crate::' + item['module'], assignment['rust_name']


def prepare(project, state, sid):
    ensure_slice(state, sid)
    base = project / '.ctrust' / 'artifacts' / sid
    base.mkdir(parents=True, exist_ok=True)
    (project / '.ctrust' / 'evidence').mkdir(parents=True, exist_ok=True)
    import yaml
    for stage in STAGES:
        path = base / (stage + '.yaml')
        if path.exists():
            continue
        data = {'version': 3, 'slice': sid, 'stage': stage, 'status': 'draft', 'review': '', 'evidence': [], 'functions': owned(state, sid)}
        if stage in ('structures', 'behavior'):
            data['completion'] = dict.fromkeys(STRUCTURES if stage == 'structures' else BEHAVIOR, False)
        if stage == 'structures':
            data['structures'] = scoped_structures(state['inventory'], owned(state, sid))
            data['unresolved'] = len(data['structures'])
        if stage == 'behavior':
            functions = state['inventory']['functions']
            data['analysis'] = scoped_behavior(state['inventory'], owned(state, sid))
            data['references'] = {target: state['architecture']['plan']['assignments'][target] for fid in owned(state, sid) for target in functions[fid].get('calls', []) if target not in owned(state, sid)}
        if stage == 'organization':
            data['symbols'] = [{'function': fid, 'path': str(rust_target(project, state, fid)[0].relative_to(project)), 'module': rust_target(project, state, fid)[1], 'name': rust_target(project, state, fid)[2]} for fid in owned(state, sid)]
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return base


def confirm(project, state, sid, stage, path):
    ensure_slice(state, sid)
    if stage not in STAGES:
        raise MigrationError(f'Unknown preparation stage: {stage}')
    path = local_path(project, path)
    fingerprint = file_hash(path)
    data = read_yaml(path)
    if data.get('version') != 3 or data.get('slice') != sid or data.get('stage') != stage or data.get('status') != 'confirmed':
        raise MigrationError(f'{path}: version/slice/stage/status must identify confirmed {sid}/{stage}')
    if not isinstance(data.get('review'), str) or not data['review'].strip() or not isinstance(data.get('evidence'), list) or not data['evidence']:
        raise MigrationError(f'{path}: review and evidence are required; template presence is insufficient')
    if set(data.get('functions', [])) != set(owned(state, sid)):
        raise MigrationError(f'{path}: functions must identify all and only owned functions')
    if stage in ('structures', 'behavior'):
        for field in STRUCTURES if stage == 'structures' else BEHAVIOR:
            if data.get('completion', {}).get(field) is not True:
                raise MigrationError(f'{path}: completion.{field} must be true')
    if stage == 'structures' and (type(data.get('unresolved')) is not int or data['unresolved'] != 0):
        raise MigrationError(f'{path}: unresolved must be integer 0')
    if stage == 'organization':
        expected = [{'function': fid, 'path': str(rust_target(project, state, fid)[0].relative_to(project)), 'module': rust_target(project, state, fid)[1], 'name': rust_target(project, state, fid)[2]} for fid in owned(state, sid)]
        if data.get('symbols') != expected:
            raise MigrationError('Organization must match canonical architecture destinations')
        crate = local_path(project, state['architecture']['plan']['slices'][sid]['crate'])
        if not (project / 'Cargo.toml').is_file() or not (crate / 'Cargo.toml').is_file():
            raise MigrationError('Provision Cargo workspace and target crate before confirming organization')
        metadata = subprocess.run(['cargo', 'metadata', '--offline', '--no-deps', '--format-version', '1'], cwd=project, capture_output=True, text=True)
        if metadata.returncode:
            raise MigrationError('Cargo layout invalid before port: ' + metadata.stderr.strip())
        layout = json.loads(metadata.stdout)
        if not any(Path(package['manifest_path']).resolve() == crate / 'Cargo.toml' and package['id'] in layout['workspace_members'] for package in layout['packages']):
            raise MigrationError(f'{crate}: target crate is not a workspace member')
    token, pending = preparation_token(project, state, sid, until=stage)
    if pending:
        raise MigrationError(f'Confirm {pending} before {stage}')
    if file_hash(path) != fingerprint:
        raise MigrationError('Preparation artifact changed during validation; repeat confirmation')
    state.setdefault('preparation', {}).setdefault(sid, {})[stage] = {'path': str(path.relative_to(project)), 'hash': fingerprint, 'input': token}


def implementation(project, state, fid, path, module):
    target, expected_module, name = rust_target(project, state, fid)
    path = target if path == 'auto' else local_path(project, path)
    if path != target or module != expected_module:
        raise MigrationError(f'Canonical destination required: {target}, {expected_module}')
    sid = state['architecture']['plan']['assignments'][fid]['slice']
    token, pending = preparation_token(project, state, sid)
    if pending:
        raise MigrationError(f'Confirm {pending} before implementation')
    fingerprint = file_hash(path)
    check_source(project, state, fid, target, name)
    if file_hash(path) != fingerprint:
        raise MigrationError('Implementation changed during validation; repeat registration')
    state['progress'].setdefault(fid, {})['implementation'] = {'path': str(path.relative_to(project)), 'module': module, 'name': name, 'hash': fingerprint, 'input': token}
    # A file registration also refreshes the file revision of already registered
    # siblings, after validating their source mappings. It does not mark new
    # siblings implemented or adopt approvals under a different preparation.
    # Existing evidence stays tied to the old implementation digest and becomes
    # outdated automatically; no command needs to reset a status manually.
    for sibling in owned(state, sid):
        if sibling == fid:
            continue
        registered = state['progress'].get(sibling, {}).get('implementation')
        if registered and registered['path'] == str(path.relative_to(project)) and registered['input'] == token:
            sibling_target, _, sibling_name = rust_target(project, state, sibling)
            check_source(project, state, sibling, sibling_target, sibling_name)
            registered['hash'] = fingerprint
    if file_hash(path) != fingerprint:
        raise MigrationError('Implementation changed during registration; repeat operation')


def check_source(project, state, fid, path, name):
    if not path.is_file():
        raise MigrationError(f'Missing Rust implementation: {path}')
    code = path.read_text(encoding='utf-8')
    fn = state['inventory']['functions'][fid]
    sid = state['architecture']['plan']['assignments'][fid]['slice']
    origin = r'// ctrust:source file=' + re.escape(fn['file']) + r' line=' + str(fn['line']) + r' symbol=' + re.escape(fn['label']) + r' slice=' + re.escape(sid) + r' contract=\S+\s*\n(?:\s*#\[[^\n]+\]\s*\n)*\s*(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?fn\s+' + re.escape(name) + r'\b'
    if not re.search(origin, code):
        raise MigrationError(f'{path}: source comment immediately before fn {name} is required')
    if re.search(r'\b(?:todo|unimplemented)!|TODO_FUNCTION|FUNCTION_NAME|SLICE_ID|UNIT_NAME|(?:Ok|Err)\s*\(\s*Default::default\(\)', code):
        raise MigrationError(f'{path}: implementation contains placeholders')
    scanned = re.sub(r'//[^\n]*|/\*.*?\*/', '', code, flags=re.S)
    if re.search(r'\*mut\s|\*const\s|zenoh::|serde_json::|use\s+[^;]*(?:transport|Message_DTO|DtoMapper)', scanned):
        raise MigrationError(f'{path}: unsafe domain API or transport dependency')
    if re.search(r'\bunsafe\b', scanned) and 'SAFETY:' not in code:
        raise MigrationError(f'{path}: unsafe requires documented SAFETY invariants')


def evidence_rows(project, state, fid, path):
    try:
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise MigrationError(f'Invalid evidence {path}: {exc}') from exc
    if not rows:
        raise MigrationError('Evidence is empty')
    for row in rows:
        if not isinstance(row, dict) or row.get('function_id') != fid or row.get('result') != 'passed' or not row.get('command') or not row.get('expected_source') or 'expected' not in row or 'observed' not in row or digest(row['expected']) != digest(row['observed']):
            raise MigrationError('Evidence requires function_id, passing result, command, expected_source and matching expected/observed')
    if not {'normal', 'boundary', 'error'} <= {row.get('category') for row in rows}:
        raise MigrationError('Evidence must cover normal, boundary and error categories')
    return rows


def evidence(project, state, fid, path):
    sid = state['architecture']['plan']['assignments'][fid]['slice']
    token, pending = preparation_token(project, state, sid)
    if pending or function_step(project, state, fid, token) == 'record-implementation':
        raise MigrationError('Record current implementation before evidence')
    missing = [sibling for sibling in owned(state, sid) if function_step(project, state, sibling, token) == 'record-implementation']
    if missing:
        raise MigrationError('Complete owned implementations before testing this slice: ' + ', '.join(missing))
    path = local_path(project, path)
    fingerprint = file_hash(path)
    evidence_rows(project, state, fid, path)
    if file_hash(path) != fingerprint:
        raise MigrationError('Evidence changed during validation; repeat registration')
    record = state['progress'][fid]
    record['evidence'] = {'path': str(path.relative_to(project)), 'hash': fingerprint, 'implementation': digest(record['implementation'])}


def compare(project, state, fid):
    sid = state['architecture']['plan']['assignments'][fid]['slice']
    token, pending = preparation_token(project, state, sid)
    if pending or function_step(project, state, fid, token) in ('record-implementation', 'record-evidence'):
        raise MigrationError('Record current evidence before comparison')
    record = state['progress'][fid]
    rows = evidence_rows(project, state, fid, local_path(project, record['evidence']['path']))
    parity = all(row.get('executable_harness_available') is True and row['expected_source'] in {'legacy_execution', 'approved_fixture'} for row in rows)
    if not parity:
        allowed = {'source_analysis', 'domain_rule', 'protocol_specification', 'manual_calculation', 'approved_fixture', 'independent_reference_model', 'expert_review'}
        if 'unresolved_reference' not in {row.get('category') for row in rows} or any(row['expected_source'] not in allowed or not row.get('expected_reason') or any(row.get(k) for k in ('executable_harness_available', 'legacy_reference_available', 'legacy_tests_available')) for row in rows):
            raise MigrationError('Without executable reference: unresolved_reference, independent expectations and explicit reasons are required')
    record['comparison'] = {'evidence': digest(record['evidence']), 'classification': 'parity_verified' if parity else 'verified_without_legacy_reference', 'scope': 'legacy_parity' if parity else 'behavior_contract'}


def check_metadata(project, state, data):
    packages = {item['id']: item for item in data['packages']}
    by_manifest = {Path(item['manifest_path']).resolve(): item for item in packages.values()}
    nodes = {item['id']: item for item in data['resolve']['nodes']}
    for item in state['architecture']['plan']['slices'].values():
        manifest = local_path(project, item['crate']) / 'Cargo.toml'
        package = by_manifest.get(manifest)
        if not package or package['id'] not in data['workspace_members']:
            raise MigrationError(f'{manifest}: slice crate must be a Cargo workspace member')
        queue, visited = [(package['id'], [package['name']])], set()
        while queue:
            identity, path = queue.pop()
            if identity in visited:
                continue
            visited.add(identity)
            for dep in nodes.get(identity, {}).get('deps', []):
                target = packages[dep['pkg']]
                chain = [*path, target['name']]
                if target['name'] in {'zenoh', 'zenoh-ext', 'serde_json', 'serde'}:
                    raise MigrationError('Forbidden default domain dependency path: ' + ' -> '.join(chain))
                queue.append((target['id'], chain))


def run_gates(project, state, sid, emit=print):
    """Run under the command lock. External edits are checked before publication."""
    start_inputs = workspace_files(project)
    start_source = digest(state['inventory'])
    gates = []
    logs = project / '.ctrust' / 'logs' / uuid.uuid4().hex
    logs.mkdir(parents=True, exist_ok=True)
    for name, command in [('architecture', ['cargo', 'metadata', '--offline', '--format-version', '1']), ('fmt', ['cargo', 'fmt', '--all', '--', '--check']), ('check', ['cargo', 'check', '--workspace', '--offline']), ('test', ['cargo', 'test', '--workspace', '--offline'])]:
        emit(f'GATE START {name} scope=workspace command={" ".join(command)}', flush=True)
        started = time.monotonic()
        log = logs / (f'{sid}-{name}.log')
        try:
            # Stream output to file, report activity without buffering entire builds.
            with log.open('w', encoding='utf-8') as handle:
                process = subprocess.Popen(command, cwd=project, stdout=handle, stderr=subprocess.STDOUT)
                while True:
                    try:
                        code = process.wait(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        emit(f'GATE RUNNING {name} elapsed={time.monotonic() - started:.0f}s log={log}', flush=True)
        except OSError as exc:
            code = 127
            log.write_text(str(exc), encoding='utf-8')
        if name == 'architecture' and code == 0:
            try:
                # Cargo warnings can precede JSON because stderr is merged.
                raw = log.read_text(encoding='utf-8')
                payload = next(line for line in reversed(raw.splitlines()) if line.startswith('{'))
                check_metadata(project, state, json.loads(payload))
            except (MigrationError, ValueError, KeyError, StopIteration) as exc:
                code = 3
                with log.open('a', encoding='utf-8') as handle:
                    handle.write('\nArchitecture rejected: ' + str(exc) + '\n')
        gates.append({'gate': name, 'scope': 'workspace', 'command': command, 'result': 'passed' if code == 0 else 'failed', 'exit_code': code, 'duration_seconds': round(time.monotonic() - started, 3), 'log': str(log.relative_to(project))})
        emit(f'GATE END {name} result={gates[-1]["result"]} log={log}', flush=True)
        if code:
            break
    end_inputs = workspace_files(project)
    # Cargo may materialize its first lockfile. Existing lockfiles remain
    # immutable inputs; the newly generated lockfile is captured in the result.
    compared = dict(end_inputs)
    if 'Cargo.lock' not in start_inputs:
        compared.pop('Cargo.lock', None)
    unchanged = compared == start_inputs and source_current(state) and digest(state['inventory']) == start_source
    return {'gates': gates, 'workspace': workspace_hash(project), 'result': 'passed' if unchanged and len(gates) == 4 and all(g['result'] == 'passed' for g in gates) else 'failed', 'inputs_unchanged': unchanged}


def validate_slice(project, state, sid):
    ensure_slice(state, sid)
    if not source_current(state):
        raise MigrationError('C source changed; refresh inventory before gates')
    if not slice_token(project, state, sid):
        raise MigrationError(f'{sid}: preparation/functions/evidence are incomplete or outdated')
    for dep in dependencies(state)[sid]:
        if not slice_verified(project, state, dep):
            raise MigrationError(f'{sid}: dependency {dep} is not verified')
    crates = set()
    for fid in owned(state, sid):
        target, _, name = rust_target(project, state, fid)
        check_source(project, state, fid, target, name)
        evidence_rows(project, state, fid, local_path(project, state['progress'][fid]['evidence']['path']))
        item = state['architecture']['plan']['slices'][sid]
        crates.add(local_path(project, item['crate']))
    if not (project / 'Cargo.toml').is_file():
        raise MigrationError('Cargo.toml is required at the workspace root')
    for crate in crates:
        manifest = crate / 'Cargo.toml'
        if not manifest.is_file():
            raise MigrationError(f'Missing crate manifest: {manifest}')
        content = manifest.read_text(encoding='utf-8')
        if re.search(r'(?m)^\s*(?:zenoh(?:-ext)?|serde_json)\s*=', content):
            raise MigrationError(f'{manifest}: forbidden transport dependency')
        for line in content.splitlines():
            if re.match(r'\s*serde\s*=', line) and not re.search(r'optional\s*=\s*true', line):
                raise MigrationError(f'{manifest}: serde must be optional in the domain')
