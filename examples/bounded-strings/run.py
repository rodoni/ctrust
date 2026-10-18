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
from migration.store import read_yaml as load
from migration.execution import STRUCTURES as STRUCTURE_FIELDS, BEHAVIOR as BEHAVIOR_FIELDS

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


    def setup(self):
        import yaml
        self.command([REPO / 'sync.sh', 'generic', self.target])
        self.controller('init')
        crate = self.target / 'crates/domain'
        for directory in ('src', 'tests', 'examples'):
            (crate / directory).mkdir(parents=True, exist_ok=True)
        (self.target / 'Cargo.toml').write_text('[workspace]\nmembers = ["crates/domain"]\nresolver = "2"\n')
        (crate / 'Cargo.toml').write_text('[package]\nname = "bounded-strings"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "bounded_strings"\n')
        self.reference = self.output / 'c-reference'
        self.command(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-DDEF_WEAK(x)=', '-I', self.legacy, self.legacy / 'strlcpy.c', self.legacy / 'strlcat.c', HERE / 'harness.c', '-o', self.reference])
        self.controller('inventory', '--source', str(self.legacy), '--header', 'legacy.h')
        self.controller('architecture', '--export', str(self.output / 'architecture.yaml'))
        plan = load(self.output / 'architecture.yaml')
        state = load(self.state / 'migration.yaml')
        self.functions = {}
        for sid, symbol, module in SLICES:
            plan['slices'][sid] = {'crate': 'crates/domain', 'module': module}
            fid = next(fid for fid, fn in state['inventory']['functions'].items() if fn['label'] == symbol and fn['status'] == 'resolved')
            self.functions[symbol] = fid
            plan['assignments'][fid].update(role='port', slice=sid, rust_name=symbol)
        for fid, assignment in plan['assignments'].items():
            if fid not in self.functions.values():
                if state['inventory']['functions'][fid]['label'] != 'strlen':
                    raise RuntimeError('Unexpected dependency requires source review: ' + fid)
                assignment.update(role='adapt', reason='strlen is implemented by CStr.to_bytes().len(); external libc function is outside the port')
        (self.output / 'architecture.yaml').write_text(yaml.safe_dump(plan, sort_keys=False))
        self.controller('architecture', '--plan', str(self.output / 'architecture.yaml'), '--confirm')
        for sid, symbol, module in SLICES:
            fn = state['inventory']['functions'][self.functions[symbol]]
            origin = f'// ctrust:source file={fn["file"]} line={fn["line"]} symbol={symbol} slice={sid} contract=C-{sid}'
            (crate / 'src' / (module + '.rs')).write_text((HERE / 'rust' / (module + '.rs')).read_text().replace('// ORIGIN_COMMENT', origin))
            (crate / 'examples' / (symbol + '.rs')).write_text((HERE / 'rust/cli.rs').read_text().replace('FUNCTION_CALL', symbol))
            (crate / 'tests' / (symbol + '.rs')).write_text(self.integration_tests(symbol))
        (crate / 'src/lib.rs').write_text(''.join(f'pub mod {module};\npub use {module}::{symbol};\n' for _, symbol, module in SLICES))
        self.command(['cargo', 'fmt', '--all'], cwd=self.target)
        self.command(['cargo', 'build', '--examples', '--offline'], cwd=self.target)
        self.stage('global inventory and confirmed architecture')

    def review_slice(self, sid, symbol):
        import yaml
        self.controller('prepare', sid)
        base = self.state / 'artifacts' / sid
        self.blocked('confirm', sid, 'contract', str((base / 'contract.yaml').relative_to(self.target)))
        contract = {
            'source_length': 'bytes before first NUL; not UTF-8 characters',
            'returns': 'source length for copy; bounded initial destination length plus source length for append',
            'destination': 'write at most capacity minus one bytes then NUL when space exists; preserve untouched tail',
            'zero_capacity': 'no writes; return attempted length',
            'append_without_nul': 'return capacity plus source length and leave destination unchanged',
            'embedded_nul': 'ignore source bytes after first NUL',
            'truncation': 'return >= capacity indicates defined insufficient-capacity outcome',
            'effects': 'only caller buffer changes; no global state, allocation or I/O'
        }
        for stage in ('structures', 'behavior', 'contract', 'model', 'organization'):
            path = base / (stage + '.yaml')
            data = load(path)
            data.update(status='confirmed', review='Reviewed OpenBSD snapshot revision and complete C branches; see sources.json and contract', evidence=[f'{symbol}.c and legacy.h', str(self.output / 'sources.json')])
            if 'completion' in data:
                data['completion'] = dict.fromkeys(data['completion'], True)
            if stage == 'structures':
                if data['structures']:
                    raise RuntimeError('Unexpected aggregate structures; source review required')
                data.update(unresolved=0, pointer_semantics={'dst': 'caller-owned &mut [u8], capacity from slice length; exclusive borrow', 'src': 'caller-owned immutable &CStr, readable through NUL; retained nowhere', 'aliasing': 'buffers may not overlap under C preconditions; Rust borrows enforce separation', 'layout': 'u8 preserves non-UTF8 bytes; no ABI or shared memory'})
            if stage in ('behavior', 'contract'):
                data.update(contract=contract, preconditions=['Source readable through NUL; nonoverlapping valid buffers; actual destination extent >= capacity'], excluded_undefined_behavior=['NULL/dangling pointers', 'overlap', 'source unreadable before NUL'], intentional_differences=['Safe borrows encode valid buffers', 'DEF_WEAK symbol visibility excluded'])
            if stage == 'model':
                data.update(api=f'pub fn {symbol}(dst: &mut [u8], src: &CStr) -> usize', unsafe=False, ffi=False, error_model='Truncation indicated by returned length, not exception', adapted_calls=['strlen -> CStr.to_bytes().len()'])
            path.write_text(yaml.safe_dump(data, sort_keys=False))
            self.controller('confirm', sid, stage, str(path.relative_to(self.target)))
        self.stage('reviewed preparation chain', sid)

    def execute_slice(self, sid, symbol, module):
        fid = self.functions[symbol]
        self.review_slice(sid, symbol)
        self.controller('record-implementation', fid, 'auto', 'crate::' + module)
        status = json.loads(self.controller('status', '--json'))
        if status['next']['action'] != 'record-evidence':
            raise RuntimeError('Implementation resume did not request evidence')
        self.blocked('compare', fid)
        records = []
        executable = self.output / 'target/debug/examples' / symbol
        for index, (capacity, initial, src, category) in enumerate(self.test_cases(symbol)):
            args = [str(capacity), initial.hex(), src.hex()]
            expected = self.command([self.reference, symbol, *args], quiet=True).strip()
            observed = self.command([executable, *args], quiet=True).strip()
            if expected != observed:
                raise RuntimeError(f'{symbol} case {index}: C={expected}, Rust={observed}, input={args}')
            records.append({'function_id': fid, 'category': category, 'command': shlex.join([str(executable), *args]), 'reference_command': shlex.join([str(self.reference), symbol, *args]), 'input': args, 'expected': expected, 'observed': observed, 'expected_source': 'legacy_execution', 'executable_harness_available': True, 'result': 'passed'})
        evidence = self.state / 'evidence' / (symbol + '.ndjson')
        evidence.write_text('\n'.join(json.dumps(row) for row in records) + '\n')
        self.controller('record-evidence', fid, str(evidence.relative_to(self.target)))
        if json.loads(self.controller('resume', '--json'))['next']['action'] != 'compare':
            raise RuntimeError('Evidence resume did not request comparison')
        self.controller('compare', fid)
        self.controller('verify-slice', sid)
        before = (self.state / 'migration.yaml').read_bytes()
        self.controller('verify-slice', sid)
        if before != (self.state / 'migration.yaml').read_bytes():
            raise RuntimeError('Repeated verification changed canonical state')
        self.counts[symbol] = len(records)
        self.stage(f'C/Rust parity: {len(records)} cases; idempotent slice conclusion', sid)

    def run(self):
        try:
            self.acquire()
            self.setup()
            for sid, symbol, module in SLICES:
                self.execute_slice(sid, symbol, module)
            self.controller('finish')
            status = json.loads(self.controller('status', '--json'))
            if not status['completed']:
                raise RuntimeError('Final closure not published')
            self.controller('report')
            summary = {'status': 'passed', 'schema': 3, 'cases': self.counts, 'completed': True, 'stages': self.stages}
        except Exception as exc:
            summary = {'status': 'failed', 'cases': self.counts, 'error': str(exc)}
            raise
        finally:
            if 'summary' in locals():
                (self.output / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
                (self.output / 'REPORT.md').write_text('# ctrust v2: bounded strings\n\n```json\n' + json.dumps(summary, indent=2, ensure_ascii=False) + '\n```\n\nSee commands.ndjson, sources.json and rust/.ctrust/.\n')
                print('Report: ' + str(self.output / 'REPORT.md'), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Empty output directory; defaults to a new timestamped runs directory')
    parser.add_argument('--download', action='store_true', help='Download upstream C files directly instead of using the browser-retrieved snapshot; requires network')
    args = parser.parse_args()
    output = args.output or HERE / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    Demo(output, args.download).run()


if __name__ == '__main__':
    main()
