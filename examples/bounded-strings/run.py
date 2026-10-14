#!/usr/bin/env python3
"""Run a reviewed, reproducible two-slice C-to-Rust conversion example."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / 'tools'))
from workflow import load, save, STRUCTURE_FIELDS, BEHAVIOR_FIELDS

SLICES = [('strings-001-copy', 'strlcpy', 'copy_string'), ('strings-002-append', 'strlcat', 'append_string')]
UPSTREAM = 'https://raw.githubusercontent.com/openbsd/src/master/lib/libc/string/'


class Demo:
    def __init__(self, output, download):
        if output.exists() and any(output.iterdir()):
            raise ValueError(f'Output must be empty: {output}')
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.target = self.output / 'rust'
        self.legacy = self.output / 'legacy'
        self.target.mkdir()
        self.legacy.mkdir()
        self.env = dict(os.environ)
        # Shell controllers invoke python3; use the interpreter running this demo.
        self.env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + self.env['PATH']
        self.env['CARGO_TARGET_DIR'] = str(self.output / 'target')
        self.logs = []
        self.stages = []
        self.cases = []
        self.download = download
        self.lib_modules = []
        self.counts = {}

    @property
    def state(self):
        return self.target / '.ctrust'

    def command(self, args, cwd=None, quiet=False, expected_code=0):
        result = subprocess.run(list(map(str, args)), cwd=cwd or REPO, env=self.env, text=True, capture_output=True)
        record = {'command': shlex.join(list(map(str, args))), 'cwd': str(cwd or REPO), 'exit_code': result.returncode, 'expected_exit_code': expected_code, 'stdout': result.stdout, 'stderr': result.stderr}
        self.logs.append(record)
        (self.output / 'commands.ndjson').write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in self.logs) + '\n')
        if result.returncode != expected_code:
            raise RuntimeError(record['command'] + '\n' + result.stdout + result.stderr)
        if not quiet:
            print(f'OK (exit {result.returncode}): {record["command"]}', flush=True)
        return result.stdout

    def controller(self, command, *args):
        return self.command([self.state / 'sync.sh', command, '.', *args], cwd=self.target)

    def blocked(self, command, *args):
        self.command([self.state / 'sync.sh', command, '.', *args], cwd=self.target, expected_code=3)

    def stage(self, name, sid='workspace'):
        self.stages.append({'slice': sid, 'stage': name, 'result': 'passed'})
        print(f'[{sid}] {name}: passed', flush=True)

    def write(self, relative, data):
        save(self.state, self.state / relative, data)

    def acquire(self):
        sources = []
        for filename in ('strlcpy.c', 'strlcat.c'):
            snapshot = HERE / 'upstream' / filename
            mode = 'browser_snapshot_whitespace_normalized'
            raw = snapshot.read_bytes()
            if self.download:
                # A failure is reported rather than silently replacing a requested download.
                with urllib.request.urlopen(UPSTREAM + filename, timeout=20) as response:
                    raw = response.read()
                mode = 'direct_download'
                expected_revision = snapshot.read_text().splitlines()[0]
                if raw.decode().splitlines()[0] != expected_revision:
                    raise RuntimeError('Upstream revision changed; review C behavior before reusing the saved contract')
            (self.legacy / filename).write_bytes(raw)
            sources.append({'file': filename, 'url': UPSTREAM + filename, 'mode': mode, 'sha256': hashlib.sha256(raw).hexdigest(), 'revision': raw.decode().splitlines()[0]})
        shutil.copyfile(HERE / 'upstream/legacy.h', self.legacy / 'legacy.h')
        (self.output / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n')
        self.stage('source acquisition')

    def setup(self):
        self.command([REPO / 'sync.sh', 'generic', self.target])
        self.controller('init')
        crate = self.target / 'crates/domain'
        (crate / 'src').mkdir(parents=True)
        (crate / 'tests').mkdir()
        (crate / 'examples').mkdir()
        (self.target / 'Cargo.toml').write_text('[workspace]\nmembers = ["crates/domain"]\nresolver = "2"\n')
        (crate / 'Cargo.toml').write_text('[package]\nname = "bounded-strings"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "bounded_strings"\n')
        (crate / 'src/lib.rs').write_text('#[path = "type.rs"]\npub mod types;\n')
        (crate / 'src/type.rs').write_text('/// Byte count excluding the terminating NUL.\npub type SourceLength = usize;\n')
        state = load(self.state / 'migration.yaml')
        state['source'] = {'project_root': str(self.legacy), 'component': 'OpenBSD libc bounded string copy/append subset', 'headers': [str(self.legacy / 'legacy.h')], 'functions': [{'c_name': symbol, 'rust_name': symbol} for _, symbol, _ in SLICES]}
        state['target'].update(project_root=str(self.target), workspace_root=str(self.target), path='crates/domain')
        self.write('migration.yaml', state)
        slices = []
        for index, (sid, symbol, module) in enumerate(SLICES, 1):
            slices.append({'id': sid, 'order': index, 'status': 'planned', 'objective': 'Copy a NUL-terminated byte string into a bounded buffer' if symbol == 'strlcpy' else 'Append a NUL-terminated byte string to a bounded buffer', 'semantic_name': module, 'source': {'headers': ['legacy.h'], 'functions': [{'c_name': symbol, 'rust_name': symbol}], 'files': [symbol + '.c']}, 'dependencies': [], 'target': {'path': 'crates/domain', 'crate_name': 'bounded-strings', 'output_kind': 'library'}, 'include': [symbol], 'exclude': ['OpenBSD weak-symbol linkage, invalid/overlapping pointers and the rest of libc'], 'infrastructure': {'adapt': ['char pointers -> borrowed slices/CStr', 'strlen -> CStr.to_bytes().len()'], 'exclude': ['DEF_WEAK symbol visibility']}, 'characterization_tests': ['normal', 'zero capacity', 'exact fit', 'truncation', 'embedded NUL', 'binary bytes', 'missing destination NUL (append)'], 'completion_criteria': ['All focused and seeded differential cases match return value and every destination byte', 'Rust tests and migration gates pass', 'No unsafe or Rust dependencies']})
        self.write('plans/units/strings-slices.yaml', {'version': 1, 'unit': 'strings', 'slices': slices})
        self.controller('plan-slices')
        self.controller('reconcile')
        self.stage('scoping and workspace')
        self.reference = self.output / 'c-reference'
        compiler = ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-DDEF_WEAK(x)=', '-I', self.legacy, self.legacy / 'strlcpy.c', self.legacy / 'strlcat.c', HERE / 'harness.c', '-o', self.reference]
        self.command(compiler)
        self.stage('executable C reference')
        database = [{'directory': str(self.legacy), 'file': str(self.legacy / (symbol + '.c')), 'arguments': ['cc', '-DDEF_WEAK(x)=', '-c', str(self.legacy / (symbol + '.c'))]} for _, symbol, _ in SLICES]
        (self.output / 'compile_commands.json').write_text(json.dumps(database, indent=2) + '\n')
        self.command([sys.executable, REPO / 'tools/header_dependency_analyzer/analyze.py', '--project', self.legacy, '--header', self.legacy / 'legacy.h', '--compile-commands', self.output / 'compile_commands.json', '--output', self.output / 'analysis'])
        graph = json.loads((self.output / 'analysis/dependencies.json').read_text())
        self.write('inventory/call-graph.yaml', {'version': 1, 'source': 'header_dependency_analyzer', 'graph': graph, 'classifications': [{'symbol': 'strlen', 'strategy': 'adapt', 'reason': 'C libc builtin; equivalent length is available from the borrowed CStr'}]})

    def inventory(self, sid, symbol):
        inventory = self.state / f'inventory/slices/{sid}'
        self.command([sys.executable, self.state / 'tools/header_dependency_analyzer/export_inventory.py', '--project', self.legacy, '--header', self.legacy / 'legacy.h', '--function', symbol, '--slice', sid, '--output', inventory, '--compile-commands', self.output / 'compile_commands.json', '--cache-dir', self.state / 'cache/header_dependency_analyzer', '--mode', 'compact'])
        self.stage('inventory', sid)
        self.blocked('contract', sid)
        if (self.state / f'contracts/units/{sid}.yaml').exists():
            raise RuntimeError('Contract created before structure/behavior confirmation')
        self.stage('gate blocks premature contract', sid)
        self.controller('reconcile')
        if load(self.state / 'migration.yaml')['phase'] != 'data_structure_mapping':
            raise RuntimeError('Inventory did not route to data-structure mapping')
        structures = load(inventory / 'data-structures.yaml')
        if structures['structures']:
            raise RuntimeError('Unexpected structure found; review needed')
        structures.update(status='confirmed', no_structures=True, completion=dict.fromkeys(STRUCTURE_FIELDS, True), evidence=[f'{symbol}.c: no struct/union/global state; source and destination are caller-owned byte arrays'], pointer_semantics={'dst': {'c': 'char * + dsize', 'rust': '&mut [u8]', 'ownership': 'caller-owned; borrowed exclusively; capacity from slice length'}, 'src': {'c': 'const char * (readable until NUL)', 'rust': '&CStr', 'ownership': 'caller-owned immutable borrowed data'}, 'aliasing': 'C source/destination overlap excluded; Rust borrows enforce separation', 'lifetime': 'no pointers retained', 'nullability': 'invalid pointers are outside the C contract; safe Rust references prohibit them', 'layout': 'u8 preserves each byte including non-UTF8; no ABI/FFI representation required', 'shared_memory': 'none; independent local buffers'})
        self.write(f'inventory/slices/{sid}/data-structures.yaml', structures)
        self.write(f'inventory/slices/{sid}/mapping-confirmation.yaml', {'version': 1, 'slice': sid, 'status': 'confirmed', 'counts': {'reachable': 0, 'classified': 0, 'excluded': 0, 'unresolved': 0}, 'mappings': [], 'exclusions': [], 'evidence': ['No aggregate structures in the selected API or function body; pointer/array mapping recorded separately']})
        self.controller('reconcile')
        if load(self.state / 'migration.yaml')['phase'] != 'behavior_extraction':
            raise RuntimeError('Structure confirmation did not route to behavior extraction')
        self.stage('data structures and pointer ownership', sid)
        fmap = load(inventory / 'function-map.yaml')
        self.write(f'inventory/slices/{sid}/function-map.generated.yaml', fmap)
        adapted = [f for f in fmap['functions'] if f['c_name'] != symbol]
        if any(f['c_name'] != 'strlen' for f in adapted):
            raise RuntimeError(f'Unclassified dependency: {adapted}')
        fmap['functions'] = [f for f in fmap['functions'] if f['c_name'] == symbol]
        fmap['adapted_dependencies'] = [{'c_name': f['c_name'], 'strategy': 'adapt', 'rust_equivalent': 'CStr.to_bytes().len()', 'reason': 'Standard-library capability, not a function in the selected port scope'} for f in adapted]
        dependencies = load(inventory / 'dependencies.yaml')
        dependencies['classifications'] = fmap['adapted_dependencies']
        self.write(f'inventory/slices/{sid}/dependencies.yaml', dependencies)
        fmap['functions'][0]['rust_name'] = symbol
        self.write(f'inventory/slices/{sid}/function-map.yaml', fmap)
        behavior = load(inventory / 'behavior.yaml')
        contract = {'source_length': 'bytes before the first NUL, not UTF-8 characters', 'returns': 'source length (copy); initial bounded destination length + source length (append)', 'truncation': 'return >= destination capacity reports insufficient room; not an exception', 'destination': 'write at most capacity-1 bytes and a NUL if there is room; preserve untouched tail bytes', 'zero_capacity': 'no destination writes; still return attempted string length', 'append_without_nul': 'if no NUL within destination capacity, return capacity + source length and leave destination unchanged', 'embedded_nul': 'ignore source bytes after the first NUL', 'side_effects': 'only caller destination buffer changes; no allocation, global state or I/O'}
        behavior.update(status='confirmed', completion=dict.fromkeys(BEHAVIOR_FIELDS, True), reviewed_contract=contract, errors=['Truncation and missing destination NUL are defined capacity failures, detected via returned length', 'Dangling/NULL pointers, unreadable non-NUL-terminated source and overlap are outside the C preconditions, not executed'], test_cases=['normal', 'boundary', 'error'], evidence=[f'{symbol}.c reviewed; executable reference built', 'Source license/revision and SHA256 recorded in sources.json'], open_questions=[])
        self.write(f'inventory/slices/{sid}/behavior.yaml', behavior)
        self.controller('reconcile')
        self.stage('behavior extraction', sid)
        self.controller('contract', sid)
        self.blocked('model', sid)
        self.stage('gate blocks model before contract confirmation', sid)
        self.write(f'contracts/units/{sid}.yaml', {'version': 1, 'id': 'C-' + sid, 'unit': sid, 'status': 'confirmed', 'source': {'files': [symbol + '.c'], 'symbol': symbol}, 'preconditions': ['Source readable through NUL; nonoverlapping valid buffers; actual destination extent >= declared capacity'], 'contract': contract, 'intentional_differences': ['Safe Rust references replace invalid pointers; destination capacity derives from slice length', 'DEF_WEAK linkage is excluded from the business behavior'], 'verification_scope': 'Parity only for defined C inputs; no equivalence claim for undefined C pointer behavior', 'open_questions': []})
        self.controller('reconcile')
        self.stage('contract confirmation', sid)
        self.controller('model', sid)
        self.blocked('select-function', sid)
        self.stage('gate blocks implementation with draft model', sid)
        model = load(self.state / f'plans/units/{sid}-model.yaml')
        model.update(status='confirmed', source={'project_root': str(self.legacy), 'files': [symbol + '.c'], 'data_structures': f'inventory/slices/{sid}/data-structures.yaml'}, target={'workspace_root': str(self.target), 'project_root': str(self.target), 'path': 'crates/domain', 'crate_name': 'bounded-strings', 'workspace_member': True}, contract=f'contracts/units/{sid}.yaml', rust_types=[{'rust_type': '&mut [u8]', 'role': 'exclusively borrowed destination'}, {'rust_type': '&CStr', 'role': 'validated NUL-terminated source bytes'}, {'rust_type': 'usize', 'role': 'returned byte length'}], api={'functions': [{'name': symbol, 'rust_signature': f'pub fn {symbol}(dst: &mut [u8], src: &CStr) -> usize', 'ownership': 'borrowed inputs; no ownership transfer', 'unsafe': False, 'ffi_required': False, 'errors': ['Truncation indicated by returned length >= dst.len()']}]}, mapping_decisions={'globals': [], 'pointers': ['borrowed byte slices and CStr'], 'macros': ['DEF_WEAK excluded'], 'calls': ['strlen adapted through CStr byte length']}, validation={'characterization_tests': ['normal', 'boundary', 'error'], 'equivalence_checks': ['return value and full buffer comparison against executable C'], 'completion_criteria': ['all cases and gates pass']}, open_questions=[])
        self.write(f'plans/units/{sid}-model.yaml', model)
        self.controller('reconcile')
        self.stage('Rust model confirmation', sid)
        self.controller('organize')
        self.controller('validate-organization')
        plan = load(self.state / f'plans/units/{sid}-organization.yaml')
        if len(plan['slices']) != 1:
            raise RuntimeError('Organization plan does not cover exactly the current slice')
        self.controller('reconcile')
        self.stage('module organization', sid)
        return fmap['functions'][0], plan['slices'][0]

    def implement(self, sid, symbol, module, function, planned):
        self.controller('select-function', sid)
        state = load(self.state / 'migration.yaml')
        unit = state['active_unit']
        crate = self.target / 'crates/domain'
        implementation = crate / planned['implementation_file']
        source = (HERE / 'rust' / (module + '.rs')).read_text()
        origin = f"// ctrust:source file={symbol}.c line={function['definition_line']} symbol={symbol} slice={sid} contract=C-{sid}"
        implementation.write_text(source.replace('// ORIGIN_COMMENT', origin))
        self.lib_modules.append(f'pub mod {module};\npub use {module}::{symbol};\n')
        (crate / 'src/lib.rs').write_text('#[path = "type.rs"]\npub mod types;\n' + ''.join(self.lib_modules))
        (crate / 'examples' / (symbol + '.rs')).write_text((HERE / 'rust/cli.rs').read_text().replace('FUNCTION_CALL', symbol))
        tests = self.integration_tests(symbol)
        (crate / planned['integration_test_file']).write_text(tests)
        self.command(['cargo', 'fmt', '--all'], cwd=self.target)
        relative = str(implementation.relative_to(self.target))
        self.controller('record-implementation', unit, relative, planned['module_path'])
        self.controller('select-function', sid)
        state = load(self.state / 'migration.yaml')
        if state['active_unit'] != unit or state['phase'] != 'function_testing':
            raise RuntimeError('Resume after implementation lost the unit/test stage')
        self.stage('implementation and recovery', sid)
        self.blocked('compare', unit, 'passed')
        self.stage('gate blocks comparison without test evidence', sid)
        self.command(['cargo', 'test', '--workspace'], cwd=self.target)
        self.command(['cargo', 'build', '--example', symbol], cwd=self.target)
        executable = self.output / 'target/debug/examples' / symbol
        records = []
        for index, case in enumerate(self.test_cases(symbol)):
            capacity, initial, src, category = case
            args = [str(capacity), initial.hex(), src.hex()]
            expected = self.command([self.reference, symbol, *args], quiet=True).strip()
            observed = self.command([executable, *args], quiet=True).strip()
            if expected != observed:
                raise RuntimeError(f'{symbol} case {index} mismatch: C={expected}, Rust={observed}, input={args}')
            records.append({'schema': 1, 'evidence_id': f'{sid}-{index:04d}', 'slice_id': sid, 'unit_id': unit, 'function_id': function['function_id'], 'category': category, 'kind': 'differential_execution', 'expected_source': 'legacy_execution', 'legacy_reference_available': True, 'legacy_tests_available': True, 'executable_harness_available': True, 'command': shlex.join([str(executable), *args]), 'reference_command': shlex.join([str(self.reference), symbol, *args]), 'input': {'capacity': capacity, 'initial_hex': initial.hex(), 'source_hex': src.hex()}, 'expected': expected, 'observed': observed, 'result': 'passed', 'source_mapping_revision': 1})
        self.counts[symbol] = len(records)
        evidence = self.state / f'evidence/tests/{unit}.ndjson'
        evidence.write_text('\n'.join(json.dumps(row) for row in records) + '\n')
        self.controller('record-evidence', unit, str(evidence.relative_to(self.target)))
        self.controller('select-function', sid)
        if load(self.state / 'migration.yaml')['phase'] != 'function_comparison':
            raise RuntimeError('Resume after evidence lost comparison stage')
        self.controller('compare', unit, 'passed')
        self.stage(f'C/Rust differential parity ({len(records)} cases)', sid)
        self.controller('verify-slice', sid)
        self.stage('verification gates and checkpoint', sid)

    @staticmethod
    def test_cases(symbol):
        # Explicit cases exercise empty strings, zero/one capacity, exact fit,
        # truncation, embedded NUL, binary data and nonterminated destination.
        focused = [(8, b'abc\0', b'xy', 'normal'), (8, b'\0', b'hello', 'normal'), (0, b'', b'abc', 'boundary'), (1, b'\0', b'abc', 'error'), (4, b'\0', b'abc', 'boundary'), (3, b'\0', b'abc', 'error'), (8, b'abc\0', b'', 'boundary'), (8, b'\0', b'a\0ignored', 'boundary'), (8, b'\0', b'\xff\x80\x01', 'normal'), (4, b'abcd', b'x', 'error'), (5, b'ab\0', b'cd', 'boundary'), (4, b'ab\0', b'cd', 'error')]
        rng = random.Random(917 if symbol == 'strlcpy' else 918)
        for _ in range(150):
            capacity = rng.randrange(0, 49)
            initial = bytes(rng.randrange(256) for _ in range(capacity))
            if capacity and rng.randrange(2):
                pos = rng.randrange(capacity)
                initial = initial[:pos] + b'\0' + initial[pos + 1:]
            source = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 65)))
            source_len = len(source.split(b'\0', 1)[0])
            initial_len = len(initial.split(b'\0', 1)[0]) if symbol == 'strlcat' else 0
            category = 'boundary' if capacity == 0 or not source_len else 'error' if initial_len + source_len >= capacity else 'normal'
            focused.append((capacity, initial, source, category))
        return focused

    @staticmethod
    def integration_tests(symbol):
        if symbol == 'strlcpy':
            cases = [(b'123456', b'ab\0', b'ab\x00456', 2), (b'xx', b'abc\0', b'a\0', 3), (b'', b'abc\0', b'', 3), (b'xxx', b'\0', b'\0xx', 0), (b'xxxxx', b'a\0ignored\0', b'a\0xxx', 1)]
        else:
            cases = [(b'ab\0xxx', b'cd\0', b'abcd\0x', 4), (b'ab\0x', b'cd\0', b'abc\0', 4), (b'abcd', b'x\0', b'abcd', 5), (b'', b'abc\0', b'', 3), (b'a\0xxx', b'b\0ignored\0', b'ab\0xx', 2)]
        def array(value):
            return '&[' + ', '.join(map(str, value)) + ']'
        result = ['use std::ffi::CStr;', f'use bounded_strings::{symbol};', '#[test]', 'fn focused_contract_cases() {']
        for initial, src, expected, length in cases:
            result += [f'let mut dst = ({array(initial)}).to_vec();', f'let src = CStr::from_bytes_until_nul({array(src)}).unwrap();', f'assert_eq!({symbol}(&mut dst, src), {length});', f'assert_eq!(dst, ({array(expected)}).to_vec());']
        result.append('}')
        return '\n'.join(result) + '\n'

    def report(self, success, error=''):
        summary = {'status': 'passed' if success else 'failed', 'cases': self.counts, 'stages': self.stages, 'error': error, 'scope': ['strlcpy', 'strlcat'], 'limitations': ['Only a two-function OpenBSD libc subset; not a whole-libc migration', 'Defined C inputs only; no invalid-pointer/overlap/unterminated-source parity claim', 'Default C snapshot retrieved through web browsing and whitespace normalized; see sources.json', 'Final workspace closure remains a review step; slice statuses are verified']}
        (self.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        lines = ['# Resultado do teste ctrust', '', f"Resultado: **{summary['status']}**", '', '| Slice | Etapa | Resultado |', '|---|---|---|']
        lines.extend(f"| {stage['slice']} | {stage['stage']} | {stage['result']} |" for stage in self.stages)
        lines += ['', f'Comparações C/Rust aprovadas: {sum(self.counts.values())}.', '', 'Artefatos:', '', '- `analysis/hierarchy.html`: grafo interativo das chamadas C.', '- `rust/.ctrust/status/slices.md`: estado dos slices.', '- `rust/.ctrust/inventory/slices/`: inventários e mapas de funções.', '- `rust/.ctrust/contracts/units/`: contratos revisados.', '- `rust/.ctrust/plans/units/`: modelos e organização Rust.', '- `rust/.ctrust/evidence/tests/`: resultados individuais C/Rust.', '- `rust/.ctrust/checkpoints/`: checkpoints de verificação.', '- `commands.ndjson`: comandos, saída, erros e código de retorno.', '- `sources.json`: URLs, modo de aquisição, revisão e hashes.', '', 'Limites:', '']
        lines.extend('- ' + value for value in summary['limitations'])
        if error:
            lines += ['', 'Falha:', '', '```text', error, '```']
        (self.output / 'REPORT.md').write_text('\n'.join(lines) + '\n')
        print(f'Report: {self.output / "REPORT.md"}', flush=True)

    def run(self):
        try:
            self.acquire()
            self.setup()
            for sid, symbol, module in SLICES:
                state = load(self.state / 'migration.yaml')
                if state['current_unit'] != sid:
                    raise RuntimeError(f'Wrong active slice: {state["current_unit"]}, expected {sid}')
                fn, planned = self.inventory(sid, symbol)
                self.implement(sid, symbol, module, fn, planned)
            self.command([self.state / 'scripts/migration-gate.sh', '.', '--all'], cwd=self.target)
            self.controller('reconcile-artifacts')
            self.controller('validate-artifacts')
            self.controller('resume', '--brief')
            manifest = load(self.state / 'plans/units/strings-slices.yaml')
            if any(item['status'] != 'verified' for item in manifest['slices']):
                raise RuntimeError('Some slice was not verified')
            self.stage('all-slice audit and artifact validation')
            self.report(True)
        except Exception as exc:
            if (self.state / 'migration.yaml').is_file():
                try:
                    self.controller('failed', str(exc).splitlines()[0])
                    self.controller('checkpoint', 'demo-failed')
                except Exception:
                    pass
            self.report(False, str(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Empty output directory; defaults to a new timestamped runs directory')
    parser.add_argument('--download', action='store_true', help='Download upstream C files directly instead of using the browser-retrieved snapshot; requires network')
    args = parser.parse_args()
    output = args.output or HERE / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    Demo(output, args.download).run()


if __name__ == '__main__':
    main()
