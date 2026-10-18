#!/usr/bin/env python3
"""Reviewed schema-3 example: two consumers, one base function, real C/Rust evidence."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]


def run(output):
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be empty; existing results are never removed')
    output.mkdir(parents=True, exist_ok=True)
    legacy, project = output / 'legacy', output / 'rust'
    legacy.mkdir()
    project.mkdir()
    env = {**os.environ, 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'], 'CARGO_TARGET_DIR': str(output / 'target')}
    commands = []

    def command(args, cwd=None):
        result = subprocess.run(list(map(str, args)), cwd=cwd or REPO, env=env, capture_output=True, text=True)
        commands.append({'command': shlex.join(list(map(str, args))), 'cwd': str(cwd or REPO), 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        (output / 'commands.ndjson').write_text('\n'.join(json.dumps(row) for row in commands) + '\n')
        if result.returncode:
            raise RuntimeError(commands[-1]['command'] + '\n' + result.stdout + result.stderr)
        return result.stdout.strip()

    def cli(name, *args):
        return command([project / '.ctrust/sync.sh', name, '.', *args], project)

    def state():
        return yaml.safe_load((project / '.ctrust/migration.yaml').read_text())

    (legacy / 'api.h').write_text('int common(int); int first(int); int second(int);\n')
    bodies = {'common': 'if (x < 0) return -1; return x + 1;', 'first': 'return common(x);', 'second': 'int result = common(x); return result < 0 ? result : result + 1;'}
    for name, body in bodies.items():
        (legacy / (name + '.c')).write_text('#include "api.h"\nint ' + name + '(int x) { ' + body + ' }\n')
    harness = output / 'reference.c'
    harness.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "api.h"\nint main(int argc, char **argv) { if (argc != 3) return 2; int x = atoi(argv[2]); int value = strcmp(argv[1], "common") == 0 ? common(x) : strcmp(argv[1], "first") == 0 ? first(x) : second(x); printf("%d", value); }\n')
    reference = output / 'reference'
    command(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-I', legacy, *[legacy / (name + '.c') for name in bodies], harness, '-o', reference])
    command([REPO / 'sync.sh', 'generic', project])
    cli('init')
    cli('inventory', '--source', str(legacy), '--header', 'api.h', '--function', 'first', '--function', 'second')
    plan_path = output / 'architecture.yaml'
    cli('architecture', '--export', str(plan_path))
    plan = yaml.safe_load(plan_path.read_text())
    plan['slices'] = {sid: {'crate': '.', 'module': sid} for sid in ('base', 'first', 'second')}
    ids = {fn['label']: fid for fid, fn in state()['inventory']['functions'].items()}
    for name, fid in ids.items():
        plan['assignments'][fid].update(slice='base' if name == 'common' else name, rust_name=name)
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False))
    cli('architecture', '--plan', str(plan_path), '--confirm')
    src = project / 'src'
    (src / 'bin').mkdir(parents=True)
    (project / 'Cargo.toml').write_text('[package]\nname="shared-functions"\nversion="0.1.0"\nedition="2021"\n')
    (src / 'lib.rs').write_text('pub mod base;\npub mod first;\npub mod second;\n#[cfg(test)]\nmod tests {\n#[test]\nfn shared_contract() { assert_eq!(crate::first::first(3), 4); assert_eq!(crate::second::second(3), 5); assert_eq!(crate::second::second(-1), -1); }\n}\n')
    rust = {'common': 'if x < 0 { -1 } else { x + 1 }', 'first': 'crate::base::common(x)', 'second': 'let result = crate::base::common(x); if result < 0 { result } else { result + 1 }'}
    for name, body in rust.items():
        sid = 'base' if name == 'common' else name
        fn = state()['inventory']['functions'][ids[name]]
        (src / (sid + '.rs')).write_text(f'// ctrust:source file={fn["file"]} line={fn["line"]} symbol={name} slice={sid} contract=C-{sid}\npub fn {name}(x: i32) -> i32 {{ {body} }}\n')
    (src / 'bin/probe.rs').write_text('fn main() { let args: Vec<String> = std::env::args().collect(); let x: i32 = args[2].parse().unwrap(); let value = match args[1].as_str() { "common" => shared_functions::base::common(x), "first" => shared_functions::first::first(x), "second" => shared_functions::second::second(x), _ => panic!("unknown function") }; print!("{value}"); }\n')
    command(['cargo', 'fmt', '--all'], project)
    command(['cargo', 'build', '--offline', '--bin', 'probe'], project)
    probe = output / 'target/debug/probe'
    cases = [(3, 'normal'), (0, 'boundary'), (2147483645, 'boundary'), (-1, 'error')]
    counts = {}
    for sid, name in [('base', 'common'), ('first', 'first'), ('second', 'second')]:
        print(f'Executing {sid}: owned function {name}', flush=True)
        cli('prepare', sid)
        for stage in ('structures', 'behavior', 'contract', 'model', 'organization'):
            path = project / '.ctrust/artifacts' / sid / (stage + '.yaml')
            data = yaml.safe_load(path.read_text())
            data.update(status='confirmed', review='Reviewed fixture: negatives return -1; common adds 1; first reuses common; second preserves error or adds 1', evidence=[f'{name}.c and executable C reference'], preconditions=['x <= 2147483645 prevents signed overflow in all consumers'], error_model='negative input returns -1; defined behavior', ownership='owned i32 values, no pointers, globals or side effects', shared_reference='common has only base as owner; consumers call crate::base::common')
            if 'completion' in data:
                data['completion'] = dict.fromkeys(data['completion'], True)
            if stage == 'structures':
                data['unresolved'] = 0
            path.write_text(yaml.safe_dump(data, sort_keys=False))
            cli('confirm', sid, stage, str(path.relative_to(project)))
        cli('record-implementation', ids[name], 'auto', 'crate::' + sid)
        rows = []
        for x, category in cases:
            expected = command([reference, name, x])
            observed = command([probe, name, x])
            if expected != observed:
                raise RuntimeError(f'Differential failure {name}({x}): C={expected} Rust={observed}')
            rows.append({'function_id': ids[name], 'command': shlex.join([str(probe), name, str(x)]), 'reference_command': shlex.join([str(reference), name, str(x)]), 'category': category, 'input': x, 'expected': expected, 'observed': observed, 'expected_source': 'legacy_execution', 'executable_harness_available': True, 'result': 'passed'})
        evidence = project / '.ctrust/evidence' / (name + '.ndjson')
        evidence.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        cli('record-evidence', ids[name], str(evidence.relative_to(project)))
        cli('compare', ids[name])
        cli('verify-slice', sid)
        counts[name] = len(rows)
    cli('finish')
    final = json.loads(cli('status', '--json'))
    assert final['completed'] and final['totals']['verified'] == 3
    assert final['slices'][1]['references'] == [ids['common']]
    assert final['slices'][2]['references'] == [ids['common']]
    cli('report')
    summary = {'status': 'passed', 'cases': counts, 'state': final, 'shared_function_implementations': 1}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    (output / 'REPORT.md').write_text('# ctrust v2: shared function\n\nOne base implementation, two consumers, 12 executed C/Rust comparisons.\n\n```json\n' + json.dumps(summary, indent=2) + '\n```\n')
    print(output / 'REPORT.md')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args().output)
