"""Schema-3 acceptance tests: shared ownership, resume, failure and publication."""
import copy
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
from migration.cli import main
from migration.core import draft_plan, decide, report, preparation_token, slice_order
from migration.inventory import collect
from migration.store import Store, MigrationError, digest, file_hash
from migration.execution import prepare, confirm, implementation, evidence, compare, rust_target, validate_slice


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'rust'
        self.source = self.root / 'legacy'
        self.source.mkdir()
        (self.source / 'api.h').write_text('int first(int x); int second(int x);\n')
        (self.source / 'api.c').write_text('int common(int x) { return x + 1; }\nint first(int x) { return common(x); }\nint second(int x) { return common(x) + 1; }\n')
        self.store = Store(self.project)
        self.state = self.store.initialize()
        self.state['source'] = {'root': str(self.source)}
        self.state['inventory'] = collect(self.source, ['api.h'])
        self.plan = draft_plan(self.state)
        self.ids = {fn['label']: fid for fid, fn in self.state['inventory']['functions'].items()}
        self.plan['slices'] = {sid: {'crate': '.', 'module': sid} for sid in ('base', 'first', 'second')}
        for name, fid in self.ids.items():
            self.plan['assignments'][fid]['slice'] = 'base' if name == 'common' else name
        self.state['architecture'] = {'plan': self.plan, 'fingerprint': digest(self.plan)}
        (self.project / 'Cargo.toml').write_text('[package]\nname="migration-test"\nversion="0.1.0"\nedition="2021"\n')
        (self.project / 'src').mkdir()
        (self.project / 'src/lib.rs').write_text('pub mod base;\npub mod first;\npub mod second;\n')

    def command(self, *args, success=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([args[0], str(self.project), *args[1:]])
        self.assertEqual(code == 0, success, stdout.getvalue() + stderr.getvalue())
        return stdout.getvalue() + stderr.getvalue()

    def ready(self, sid):
        base = prepare(self.project, self.state, sid)
        for stage in ('structures', 'behavior', 'contract', 'model', 'organization'):
            path = base / (stage + '.yaml')
            data = yaml.safe_load(path.read_text())
            data.update(status='confirmed', review='Reviewed source branches and ownership', evidence=['api.c source analysis'])
            if 'completion' in data:
                data['completion'] = dict.fromkeys(data['completion'], True)
            if stage == 'structures':
                data['unresolved'] = 0
            path.write_text(yaml.safe_dump(data, sort_keys=False))
            confirm(self.project, self.state, sid, stage, str(path.relative_to(self.project)))

    def implement(self, name):
        fid = self.ids[name]
        path, module, rust_name = rust_target(self.project, self.state, fid)
        fn = self.state['inventory']['functions'][fid]
        sid = self.plan['assignments'][fid]['slice']
        expression = {'common': 'x + 1', 'first': 'crate::base::common(x)', 'second': 'crate::base::common(x) + 1'}[name]
        path.write_text(f'// ctrust:source file={fn["file"]} line={fn["line"]} symbol={name} slice={sid} contract=C-{sid}\npub fn {rust_name}(x: i32) -> i32 {{\n    {expression}\n}}\n')
        implementation(self.project, self.state, fid, 'auto', module)

    def record(self, name, categories=('normal', 'boundary', 'error'), parity=True):
        fid = self.ids[name]
        path = self.project / (name + '.ndjson')
        rows = [{'function_id': fid, 'command': 'reference comparison', 'result': 'passed', 'category': category, 'expected': 2, 'observed': 2, 'expected_source': 'legacy_execution' if parity else 'manual_calculation', 'executable_harness_available': parity, 'expected_reason': '' if parity else 'No executable reference; calculation from reviewed domain rule'} for category in categories]
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        evidence(self.project, self.state, fid, str(path.relative_to(self.project)))
        return path

    def verified(self, sid, name):
        self.ready(sid)
        self.implement(name)
        self.record(name)
        compare(self.project, self.state, self.ids[name])
        self.store.publish(self.state)
        gate_result = {'result': 'passed', 'inputs_unchanged': True, 'workspace': 'example', 'gates': []}
        with patch('migration.cli.run_gates', return_value=gate_result):
            self.command('verify-slice', sid)
        self.state = self.store.read()

    def test_shared_function_has_one_owner_and_dependencies_precede_consumers(self):
        self.assertEqual(slice_order(self.state), ['base', 'first', 'second'])
        self.assertEqual(len(self.state['inventory']['functions']), 3)
        self.assertEqual(decide(self.project, self.state)['slice'], 'base')
        self.verified('base', 'common')
        status = report(self.project, self.state)
        self.assertEqual(status['totals']['verified'], 1)
        self.assertEqual(status['slices'][1]['references'], [self.ids['common']])
        self.assertEqual(status['next']['slice'], 'first')

    def test_resume_uses_valid_function_milestone_and_queries_do_not_write(self):
        self.ready('base')
        self.implement('common')
        self.store.publish(self.state)
        before = self.store.path.read_bytes()
        output = self.command('resume', '--json')
        self.assertEqual(json.loads(output)['next']['action'], 'record-evidence')
        self.assertEqual(before, self.store.path.read_bytes())
        self.record('common')
        self.assertEqual(decide(self.project, self.state)['action'], 'compare')
        compare(self.project, self.state, self.ids['common'])
        self.assertEqual(decide(self.project, self.state)['action'], 'verify-slice')

    def test_multiple_functions_in_one_file_converge_before_tests(self):
        self.plan['assignments'][self.ids['first']]['slice'] = 'base'
        del self.plan['slices']['first']
        self.state['architecture']['fingerprint'] = digest(self.plan)
        self.ready('base')
        self.implement('common')
        decision = decide(self.project, self.state)
        self.assertEqual((decision['action'], decision['function']), ('record-implementation', self.ids['first']))
        path, module, _ = rust_target(self.project, self.state, self.ids['first'])
        fn = self.state['inventory']['functions'][self.ids['first']]
        with path.open('a') as handle:
            handle.write(f'\n// ctrust:source file={fn["file"]} line={fn["line"]} symbol=first slice=base contract=C-base\npub fn first(x: i32) -> i32 {{ common(x) }}\n')
        implementation(self.project, self.state, self.ids['first'], 'auto', module)
        for name in ('common', 'first'):
            self.assertEqual(self.state['progress'][self.ids[name]]['implementation']['hash'], file_hash(path))
        self.assertEqual(decide(self.project, self.state)['action'], 'record-evidence')
        self.record('common')
        compare(self.project, self.state, self.ids['common'])
        self.assertEqual(decide(self.project, self.state)['function'], self.ids['first'])
        self.assertEqual(decide(self.project, self.state)['action'], 'record-evidence')
        path.write_text(path.read_text().replace('x + 1', 'x + 2'))
        implementation(self.project, self.state, self.ids['common'], 'auto', module)
        self.assertEqual(decide(self.project, self.state)['action'], 'record-evidence')

    def test_invalid_evidence_preserves_implementation_and_is_correctable(self):
        self.ready('base')
        self.implement('common')
        self.store.publish(self.state)
        original = copy.deepcopy(self.state['progress'])
        (self.project / 'bad.ndjson').write_text('{}')
        self.command('record-evidence', self.ids['common'], 'bad.ndjson', success=False)
        self.assertEqual(self.store.read()['progress'], original)
        self.state = self.store.read()
        path = self.record('common')
        self.command('record-evidence', self.ids['common'], path.name)
        self.assertFalse(self.store.read()['issues'])

    def test_repeated_records_and_finalization_are_idempotent(self):
        self.ready('base')
        self.implement('common')
        self.store.publish(self.state)
        before = self.store.path.read_bytes()
        self.command('record-implementation', self.ids['common'], 'auto', 'crate::base')
        self.assertEqual(before, self.store.path.read_bytes())
        self.record('common')
        compare(self.project, self.state, self.ids['common'])
        self.store.publish(self.state)
        with patch('migration.cli.run_gates', return_value={'result': 'passed', 'inputs_unchanged': True, 'workspace': 'w', 'gates': []}):
            self.command('verify-slice', 'base')
        before = self.store.path.read_bytes()
        checkpoints = list((self.store.root / 'checkpoints').iterdir())
        with patch('migration.cli.run_gates', side_effect=AssertionError('duplicate gate')):
            self.command('verify-slice', 'base')
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(checkpoints, list((self.store.root / 'checkpoints').iterdir()))

    def test_code_changes_invalidate_evidence_and_contract_changes_invalidate_implementation(self):
        self.ready('base')
        self.implement('common')
        self.record('common')
        compare(self.project, self.state, self.ids['common'])
        target, module, _ = rust_target(self.project, self.state, self.ids['common'])
        target.write_text(target.read_text() + '// implementation revision\n')
        self.assertEqual(decide(self.project, self.state)['action'], 'record-implementation')
        implementation(self.project, self.state, self.ids['common'], 'auto', module)
        self.assertEqual(decide(self.project, self.state)['action'], 'record-evidence')
        contract = self.store.root / 'artifacts/base/contract.yaml'
        contract.write_text(contract.read_text() + '\nrevision_notes: amended\n')
        self.assertEqual(decide(self.project, self.state)['stage'], 'contract')

    def test_architecture_rejects_cycle_unresolved_and_excluded_dependencies(self):
        from migration.core import architecture_errors
        self.plan['assignments'][self.ids['common']]['role'] = 'exclude'
        self.plan['assignments'][self.ids['common']]['reason'] = 'unnecessary'
        self.assertTrue(any('excluded' in e for e in architecture_errors(self.state, self.plan)))
        self.plan['assignments'][self.ids['common']]['role'] = 'port'
        self.state['inventory']['functions'][self.ids['common']]['calls'] = [self.ids['first']]
        self.assertTrue(any('cycle' in e for e in architecture_errors(self.state, self.plan)))

    def test_cargo_metadata_rejects_activated_optional_transport_dependency(self):
        from migration.execution import check_metadata
        serde = self.root / 'serde'
        (serde / 'src').mkdir(parents=True)
        (serde / 'src/lib.rs').write_text('// local metadata fixture\n')
        (serde / 'Cargo.toml').write_text('[package]\nname="serde"\nversion="1.0.0"\nedition="2021"\n')
        manifest = self.project / 'Cargo.toml'
        base = manifest.read_text() + '\n[dependencies]\nserde = { path = "../serde", optional = true }\n'
        manifest.write_text(base + '\n[features]\ndefault = ["dep:serde"]\n')
        data = json.loads(subprocess.check_output(['cargo', 'metadata', '--offline', '--format-version', '1'], cwd=self.project, text=True, stderr=subprocess.DEVNULL))
        with self.assertRaisesRegex(MigrationError, 'migration-test -> serde'):
            check_metadata(self.project, self.state, data)
        manifest.write_text(base)
        data = json.loads(subprocess.check_output(['cargo', 'metadata', '--offline', '--format-version', '1'], cwd=self.project, text=True, stderr=subprocess.DEVNULL))
        check_metadata(self.project, self.state, data)

    def test_source_line_change_keeps_identity_but_requires_new_inventory(self):
        path = self.source / 'api.c'
        path.write_text('\n' + path.read_text())
        current = collect(self.source, ['api.h'])
        self.assertEqual(set(current['functions']), set(self.state['inventory']['functions']))
        self.assertEqual(decide(self.project, self.state)['action'], 'inventory')

    def test_snapshot_publication_failure_keeps_previous_revision(self):
        before = self.store.path.read_bytes()
        with patch('migration.store.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                with self.store.change() as state:
                    state['issues'] = {'test': {'operation': 'test', 'scope': 'workspace', 'reason': 'changed'}}
        self.assertEqual(before, self.store.path.read_bytes())

    def test_invalid_canonical_fingerprint_is_never_published(self):
        self.store.publish(self.state)
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(MigrationError, 'fingerprint mismatch'):
            with self.store.change() as state:
                state['inventory']['functions'][self.ids['common']]['label'] = 'accidental edit'
        self.assertEqual(before, self.store.path.read_bytes())

    def test_structure_preparation_is_scoped_to_owned_functions(self):
        from migration.inventory import scoped_structures
        source = self.root / 'typed'
        source.mkdir()
        (source / 'api.h').write_text('struct A { int a; }; struct B { int b; }; void first(struct A *a); void second(struct B *b);')
        (source / 'api.c').write_text('#include "api.h"\nvoid first(struct A *a) {}\nvoid second(struct B *b) {}\n')
        inventory = collect(source, ['api.h'])
        structures = scoped_structures(inventory, ['c:api.c:first'])
        self.assertEqual([item['name'] for item in structures], ['A'])

    def test_external_dependencies_do_not_become_entry_roots(self):
        from migration.core import source_current
        includes = self.root / 'includes'
        module = self.root / 'external'
        includes.mkdir()
        module.mkdir()
        (includes / 'external.h').write_text('struct Value { int x; }; int helper(struct Value *v); int unrelated(void);')
        (module / 'helper.c').write_text('#include <external.h>\nint helper(struct Value *v) { return v->x; }\nint unrelated(void) { return 0; }')
        (self.source / 'api.h').write_text('int entry(void);')
        (self.source / 'api.c').write_text('#include "external.h"\nint entry(void) { struct Value v; return helper(&v); }')
        inventory = collect(self.source, [], include_dirs=[includes], dependency_roots=[module])
        names = {fn['label'] for fn in inventory['functions'].values()}
        self.assertEqual(names, {'entry', 'helper'})
        self.assertEqual([inventory['functions'][fid]['label'] for fid in inventory['roots']], ['entry'])
        self.assertFalse(inventory['unresolved_includes'])
        state = {'source': {'root': str(self.source)}, 'inventory': inventory}
        self.assertTrue(source_current(state))
        (includes / 'external.h').write_text('int helper(void);')
        self.assertFalse(source_current(state))

    def test_inventory_change_during_analysis_is_not_published(self):
        import export_inventory
        original = export_inventory.merge_analyses
        def changing(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.source / 'api.c'
            path.write_text(path.read_text() + '\nint added(void) { return 1; }\n')
            return result
        with patch.object(export_inventory, 'merge_analyses', side_effect=changing):
            with self.assertRaisesRegex(MigrationError, 'changed during inventory'):
                collect(self.source, ['api.h'])

    def test_previous_schema_is_rejected_without_upgrade(self):
        self.store.path.write_text('version: 2\n')
        output = self.command('status', success=False)
        self.assertIn('No upgrade', output)
        self.assertEqual(self.store.path.read_text(), 'version: 2\n')

    def test_without_reference_requires_independent_expectations(self):
        self.ready('base')
        self.implement('common')
        self.record('common', parity=False)
        with self.assertRaises(MigrationError):
            compare(self.project, self.state, self.ids['common'])
        self.record('common', categories=('normal', 'boundary', 'error', 'unresolved_reference'), parity=False)
        compare(self.project, self.state, self.ids['common'])
        self.assertEqual(self.state['progress'][self.ids['common']]['comparison']['classification'], 'verified_without_legacy_reference')

    def test_gate_failure_keeps_function_progress_and_logs_scope(self):
        self.ready('base')
        self.implement('common')
        self.record('common')
        compare(self.project, self.state, self.ids['common'])
        self.store.publish(self.state)
        old_progress = copy.deepcopy(self.state['progress'])
        with patch('migration.cli.run_gates', return_value={'result': 'failed', 'inputs_unchanged': True, 'gates': [{'gate': 'check', 'scope': 'workspace', 'result': 'failed'}]}):
            self.command('verify-slice', 'base', success=False)
        state = self.store.read()
        self.assertEqual(old_progress, state['progress'])
        self.assertNotIn('base', state['slice_results'])
        self.assertEqual(state['gate_runs']['base']['gates'][0]['scope'], 'workspace')

    def test_base_evidence_correction_does_not_restart_its_consumers(self):
        for sid, name in [('base', 'common'), ('first', 'first'), ('second', 'second')]:
            self.verified(sid, name)
        before = copy.deepcopy(self.state['progress'][self.ids['first']])
        evidence_path = self.project / 'common.ndjson'
        rows = [json.loads(line) for line in evidence_path.read_text().splitlines()]
        for row in rows:
            row['review_note'] = 'Provenance detail clarified; same executed cases'
        evidence_path.write_text('\n'.join(json.dumps(row) for row in rows))
        evidence(self.project, self.state, self.ids['common'], evidence_path.name)
        compare(self.project, self.state, self.ids['common'])
        self.store.publish(self.state)
        with patch('migration.cli.run_gates', return_value={'result': 'passed', 'inputs_unchanged': True, 'gates': []}):
            self.command('verify-slice', 'base')
        state = self.store.read()
        self.assertEqual(state['progress'][self.ids['first']], before)
        self.assertTrue(all(row['verified_slice'] for row in report(self.project, state)['slices']))

    def test_checkpoint_missing_can_be_rebuilt_without_repromotion(self):
        self.verified('base', 'common')
        for path in (self.store.root / 'checkpoints').iterdir():
            path.unlink()
        before = self.store.path.read_bytes()
        self.command('verify-slice', 'base')
        self.assertTrue(list((self.store.root / 'checkpoints').iterdir()))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_html_is_standalone_and_exports_the_same_plan(self):
        from migration.html import write_report
        path = write_report(self.project, self.state)
        content = path.read_text()
        self.assertIn('Exportar plano JSON', content)
        self.assertIn(self.ids['common'], content)
        self.assertNotIn('<script src=', content)
        self.assertIn('application/json', content)

    def test_real_c_rust_shared_dependency_and_final_closure(self):
        # Materialize the reviewed implementations before Cargo checks the
        # workspace. Progress is still recorded only as each slice is executed.
        for sid, name in [('base', 'common'), ('first', 'first'), ('second', 'second')]:
            self.ready(sid)
            self.implement(name)
        self.state['preparation'] = {}
        self.state['progress'] = {}
        lib = self.project / 'src/lib.rs'
        lib.write_text(lib.read_text() + '\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn shared_behavior() {\n        assert_eq!(crate::first::first(3), 4);\n        assert_eq!(crate::second::second(3), 5);\n    }\n}\n')
        subprocess.run(['cargo', 'fmt', '--all'], cwd=self.project, check=True, capture_output=True)
        harness = self.root / 'reference.c'
        harness.write_text('#include <stdio.h>\nint first(int); int second(int); int main(void) { printf("%d %d", first(3), second(3)); }\n')
        reference = self.root / 'reference'
        subprocess.run(['cc', str(self.source / 'api.c'), str(harness), '-o', str(reference)], check=True, capture_output=True)
        self.assertEqual(subprocess.check_output([str(reference)], text=True), '4 5')
        for sid, name in [('base', 'common'), ('first', 'first'), ('second', 'second')]:
            self.ready(sid)
            self.implement(name)
            self.record(name)
            compare(self.project, self.state, self.ids[name])
            self.store.publish(self.state)
            self.command('verify-slice', sid)
            self.state = self.store.read()
        self.assertEqual(decide(self.project, self.state)['action'], 'finish')
        self.command('finish')
        before = self.store.path.read_bytes()
        self.command('finish')
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertTrue(json.loads(self.command('status', '--json'))['completed'])

    def test_public_installed_cli_inventory_selection_and_architecture(self):
        destination = self.root / 'installed project'
        env = {**os.environ, 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
        def run(*args):
            result = subprocess.run(list(map(str, args)), env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout
        run(REPO / 'sync.sh', 'generic', destination)
        controller = destination / '.ctrust/sync.sh'
        run(controller, 'init', destination)
        run(controller, 'inventory', destination, '--source', self.source, '--header', 'api.h', '--function', 'first', '--function', 'second')
        exported = self.root / 'plan.yaml'
        run(controller, 'architecture', destination, '--export', exported)
        plan = yaml.safe_load(exported.read_text())
        plan['slices'] = self.plan['slices']
        for fid, assignment in plan['assignments'].items():
            assignment['slice'] = self.plan['assignments'][fid]['slice']
        exported.write_text(yaml.safe_dump(plan))
        run(controller, 'architecture', destination, '--plan', exported, '--confirm')
        status = json.loads(run(controller, 'status', destination, '--json'))
        self.assertEqual(status['next']['slice'], 'base')
        self.assertEqual(status['totals']['selected'], 3)
        before = (destination / '.ctrust/migration.yaml').read_bytes()
        run(controller, 'report', destination)
        self.assertEqual(before, (destination / '.ctrust/migration.yaml').read_bytes())

    def test_killed_writer_leaves_a_complete_snapshot_and_releases_lock(self):
        for after in (False, True):
            with self.subTest(after_publication=after):
                code = '''
import os, signal, sys
from migration.store import Store
import migration.store as module
store = Store(sys.argv[1])
original = module.atomic_write
def interrupted(*args):
    if sys.argv[2] == 'True':
        original(*args)
    os.kill(os.getpid(), signal.SIGKILL)
module.atomic_write = interrupted
with store.change() as state:
    state['issues'] = {'writer': {'operation': 'test', 'scope': 'workspace', 'reason': 'published'}}
'''
                self.store.publish(self.state)
                result = subprocess.run([sys.executable, '-c', code, str(self.project), str(after)], env={**os.environ, 'PYTHONPATH': str(REPO / 'tools')}, capture_output=True)
                self.assertEqual(result.returncode, -9)
                current = self.store.read()
                self.assertEqual(bool(current['issues']), after)
                # Stable lock inode remains; kernel ownership is released.
                with self.store.change() as recovered:
                    recovered['issues'] = {}

    def test_changed_preparation_during_gates_prevents_promotion(self):
        self.ready('base')
        self.implement('common')
        self.record('common')
        compare(self.project, self.state, self.ids['common'])
        self.store.publish(self.state)
        def altered(*args):
            path = self.store.root / 'artifacts/base/contract.yaml'
            path.write_text(path.read_text() + '\nchanged_during_gate: true\n')
            return {'result': 'passed', 'inputs_unchanged': True, 'workspace': 'w', 'gates': []}
        with patch('migration.cli.run_gates', side_effect=altered):
            self.command('verify-slice', 'base', success=False)
        self.assertNotIn('base', self.store.read()['slice_results'])

    def test_rearchitecture_does_not_adopt_old_approvals(self):
        self.verified('base', 'common')
        changed = copy.deepcopy(self.plan)
        changed['slices']['base']['module'] = 'numeric_base'
        path = self.project / 'revised.yaml'
        path.write_text(yaml.safe_dump(changed))
        self.command('architecture', '--plan', str(path), '--confirm')
        state = self.store.read()
        self.assertEqual(decide(self.project, state)['stage'], 'structures')
        self.assertEqual(report(self.project, state)['totals']['verified'], 0)
        self.assertTrue(state['history'][-1]['previous']['architecture'])

    def test_json_boolean_is_not_numeric_evidence(self):
        self.ready('base')
        self.implement('common')
        path = self.record('common')
        rows = [json.loads(row) for row in path.read_text().splitlines()]
        rows[0].update(expected=1, observed=True)
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        with self.assertRaises(MigrationError):
            evidence(self.project, self.state, self.ids['common'], path.name)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the standalone editor behavior test')
    def test_editor_javascript_groups_exports_reopens_and_filters(self):
        from migration.html import write_report
        html = write_report(self.project, self.state).read_text()
        data = html.split('<script type="application/json" id="data">')[1].split('</script>')[0]
        script = html.split('<script>')[1].split('</script>')[0]
        harness = self.root / 'editor-test.js'
        harness.write_text(r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const payload=JSON.parse(process.argv[2]);
const nodes={}, prompts=[];let exported;
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=[...items];}
 click(){if(this.tag==='a')exported=JSON.parse(blob.parts.join(''));}
}
let blob;
const document={getElementById(id){return nodes[id]??=new Element();},createElement(tag){return new Element(tag);}};
document.getElementById('data').textContent=JSON.stringify(payload);
document.getElementById('filter').value='all';
const context={document,structuredClone,Blob:class{constructor(parts){this.parts=parts;}},URL:{createObjectURL(value){blob=value;return 'blob:test';},revokeObjectURL(){}},setTimeout(fn){fn();},prompt(){return prompts.shift();},alert(message){throw Error(message);}};
vm.runInNewContext(process.argv[3],context);
async function test(){
 assert.equal(nodes.rows.children.length,3);
 nodes.filter.value='shared';nodes.filter.onchange();assert.equal(nodes.rows.children.length,1);
 const checkbox=nodes.rows.children[0].children[0].children[0];checkbox.checked=true;checkbox.onchange();
 prompts.push('numeric','crates/domain','numeric');nodes.add.onclick();
 prompts.push('numeric');nodes.batch.onclick();prompts.push('base');nodes.remove.onclick();nodes.download.onclick();
 const common=Object.keys(exported.assignments).find(id=>id.endsWith(':common'));
 assert.equal(exported.assignments[common].slice,'numeric');
 assert.equal(exported.slices.numeric.module,'numeric');
 assert.equal(exported.slices.base,undefined);const grouped=exported;
 nodes.upload.files=[{async text(){return JSON.stringify(payload.plan);}}];await nodes.upload.onchange();
 nodes.download.onclick();assert.equal(exported.assignments[common].slice,'base');
 nodes.filter.value='all';nodes.search.value='second';nodes.search.oninput();assert.equal(nodes.rows.children.length,1);
 console.log(JSON.stringify(grouped));
}
test().catch(error=>{console.error(error);process.exitCode=1;});
''')
        result = subprocess.run(['node', str(harness), data, script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        from migration.core import architecture_errors
        self.assertEqual(architecture_errors(self.state, json.loads(result.stdout)), [])


if __name__ == '__main__':
    unittest.main()
