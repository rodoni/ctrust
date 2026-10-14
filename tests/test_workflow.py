import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
from workflow import load, INVENTORY_FIELDS


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ctrust-regression-')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.state = self.project / '.ctrust'
        self.controller = REPO / 'sync.sh'
        self.command('init')
        self.write('plans/units/demo-slices.yaml', {'version': 1, 'unit': 'demo', 'slices': [self.slice('demo-001')]})
        self.command('reconcile')

    def slice(self, sid, **values):
        return {'id': sid, 'order': 1, 'status': 'planned', 'objective': 'Parse ' + sid, 'source': {'functions': [{'c_name': 'foo'}]}, 'dependencies': [], 'characterization_tests': ['normal', 'boundary', 'error'], **values}

    def write(self, relative, data):
        path = self.state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        return path

    def read(self, relative='migration.yaml'):
        return load(self.state / relative)

    def command(self, name, *args, success=True):
        result = subprocess.run([str(self.controller), name, str(self.project), *args], text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def gate(self, name, scope='', success=True):
        result = subprocess.run([str(REPO / 'scripts' / name), str(self.project), scope], text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def ready(self, names=('foo', 'foobar'), sid='demo-001'):
        inv = f'inventory/slices/{sid}/'
        self.write(inv + 'manifest.yaml', {'slice': sid, 'summary': {}})
        self.write(inv + 'symbols.yaml', {'symbols': []})
        self.write(inv + 'files.yaml', {'files': [], 'headers': []})
        self.write(inv + 'callers.yaml', {'edges': []})
        self.write(inv + 'dependencies.yaml', {'slice': sid, 'edges': []})
        self.write(inv + 'data-structures.yaml', {'slice': sid, 'structures': [], 'status': 'confirmed', 'completion': dict.fromkeys(['structures_identified', 'fields_mapped', 'layout_mapped', 'pointer_semantics_mapped', 'shared_memory_classified', 'rust_strategy_defined', 'evidence_recorded'], True)})
        self.write(inv + 'mapping-confirmation.yaml', {'status': 'confirmed', 'counts': {'unresolved': 0}})
        self.write(inv + 'behavior.yaml', {'functions': [], 'status': 'confirmed', 'completion': dict.fromkeys(['source_logic_read', 'branches_mapped', 'calls_mapped', 'errors_mapped', 'side_effects_mapped', 'test_cases_defined'], True)})
        self.write(f'contracts/units/{sid}.yaml', {'status': 'confirmed'})
        self.write(f'plans/units/{sid}-model.yaml', {'status': 'confirmed'})
        self.write(inv + 'function-map.yaml', {'version': 2, 'functions': [{'c_name': name, 'function_id': f'c:foo.c:{i}:{name}', 'definition_file': 'foo.c', 'definition_line': i, 'state': 'planned'} for i, name in enumerate(names, 1)]})
        self.command('organize')
        planned = self.read(f'plans/units/{sid}-organization.yaml')['slices'][0]
        self.rust_file = planned['implementation_file']
        self.module_path = planned['module_path']
        src = self.project / self.rust_file
        src.parent.mkdir(exist_ok=True)
        (self.project / 'src/lib.rs').write_text(f"pub mod {planned['semantic_name']};\n")
        src.write_text('\n'.join(f'// ctrust:source file=foo.c line={i} symbol={name} slice={sid} contract=C1\npub fn {name}() -> Option<u8> {{ None }}' for i, name in enumerate(names, 1)) + '\n#[cfg(test)]\nmod tests {\n #[test]\n fn checks_none() { assert_eq!(super::foo(), None); }\n}\n')

    def evidence(self, unit, name='foo', parity=True, categories=('normal', 'boundary', 'error')):
        fn = next(f for f in self.read('inventory/slices/demo-001/function-map.yaml')['functions'] if f['c_name'] == name)
        path = self.state / 'evidence/tests' / (unit + '.ndjson')
        if not parity:
            categories = (*categories, 'unresolved_reference')
        records = [{'slice_id': 'demo-001', 'unit_id': unit, 'function_id': fn['function_id'], 'category': category, 'result': 'passed', 'command': 'cargo test', 'expected_source': 'legacy_execution' if parity else 'source_analysis', 'expected_reason': '' if parity else 'Legacy harness unavailable; expected value derived from source branch', 'executable_harness_available': parity, 'legacy_reference_available': parity, 'expected': 'None', 'observed': 'None'} for category in categories]
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        return str(path.relative_to(self.project))

    def implementation(self, unit):
        self.command('record-implementation', unit, self.rust_file, self.module_path)

    def test_exact_function_update_and_distinct_units(self):
        self.ready()
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.assertEqual(self.read()['eligible_actions'], ['record-implementation'])
        self.implementation(unit)
        self.assertEqual(self.read()['eligible_actions'], ['record-evidence'])
        states = [f['state'] for f in self.read('inventory/slices/demo-001/function-map.yaml')['functions']]
        self.assertEqual(states, ['implemented', 'planned'])
        self.command('record-evidence', unit, self.evidence(unit))
        self.assertEqual(self.read()['eligible_actions'], ['compare'])
        self.command('compare', unit, 'passed')
        self.command('select-function', 'demo-001')
        self.assertEqual(self.read()['active_functions'], ['foobar'])
        self.assertNotEqual(unit, self.read()['active_unit'])

    def test_resume_implemented_and_tested_preserves_progress(self):
        self.ready()
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('select-function', 'demo-001')
        self.assertEqual(self.read()['phase'], 'function_testing')
        self.command('record-evidence', unit, self.evidence(unit))
        self.command('reconcile')
        self.command('select-function', 'demo-001')
        self.assertEqual(self.read()['phase'], 'function_comparison')
        self.assertEqual(self.read()['test_status'], 'passed')
        self.assertEqual(self.read()['active_unit'], unit)

    def test_rejects_missing_evidence_wrong_unit_and_skipped_transitions(self):
        self.ready()
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.command('compare', unit, 'passed', success=False)
        self.command('record-implementation', 'wrong', 'src/lib.rs', 'crate::foo', success=False)
        self.command('record-implementation', unit, 'missing.rs', 'crate::foo', success=False)
        self.command('record-implementation', unit, 'src/lib.rs', 'crate::wrong', success=False)
        self.implementation(unit)
        self.command('record-evidence', unit, 'missing.ndjson', success=False)
        self.assertEqual(self.read()['test_status'], 'pending')

    def test_comparison_persists_evidence_but_waits_for_slice_gates(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('record-evidence', unit, self.evidence(unit))
        self.command('compare', unit, 'passed')
        self.assertEqual(self.read('inventory/slices/demo-001/comparison.yaml')['status'], 'passed')
        self.assertEqual(self.read('plans/units/demo-slices.yaml')['slices'][0]['status'], 'in_progress')
        self.gate('function-recovery-gate.sh')
        self.gate('migration-comparison.sh')
        (self.project / 'src/future.rs').write_text('pub fn later() { unimplemented!(); }\n')
        self.gate('migration-completeness.sh')
        self.assertTrue(self.read('inventory/slices/demo-001/function-map.yaml')['functions'][0]['evidence_refs'])

    def test_no_reference_verification_requires_reason_and_independent_cases(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('record-evidence', unit, self.evidence(unit, parity=False))
        self.command('compare', unit, 'passed')
        fn = self.read('inventory/slices/demo-001/function-map.yaml')['functions'][0]
        self.assertEqual(fn['comparison_status'], 'verified_without_legacy_reference')
        self.assertEqual(fn['verification_scope'], 'behavior_contract')
        self.gate('migration-comparison.sh')

    def test_comparison_gate_rechecks_evidence_instead_of_trusting_status(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        relative = self.evidence(unit)
        self.command('record-evidence', unit, relative)
        self.command('compare', unit, 'passed')
        path = self.project / relative
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[0]['observed'] = 'different output'
        path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
        self.gate('migration-comparison.sh', success=False)

    def test_insufficient_case_coverage_cannot_verify(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('record-evidence', unit, self.evidence(unit, categories=('normal',)))
        self.command('compare', unit, 'passed', success=False)
        self.assertEqual(self.read('inventory/slices/demo-001/function-map.yaml')['functions'][0]['state'], 'tested')

    def test_reconcile_preserves_retry_block(self):
        self.command('failed', 'same cause')
        self.command('failed', 'same cause', success=False)
        self.command('reconcile')
        self.assertEqual(self.read()['status'], 'blocked')
        self.assertEqual(self.read()['attempts'], 2)
        self.assertEqual(self.read()['last_error'], 'same cause')
        self.command('next', 'Cause fixed; resume the slice')
        self.command('reconcile')
        self.assertEqual(self.read()['status'], 'in_progress')
        self.assertEqual(self.read()['attempts'], 0)

    def test_failure_blocks_slice_and_cannot_be_reconciled_away(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.command('compare', unit, 'failed')
        self.command('reconcile')
        self.assertEqual(self.read()['status'], 'blocked')
        self.assertEqual(self.read('plans/units/demo-slices.yaml')['slices'][0]['status'], 'blocked')
        resume = self.command('resume', '--brief')
        self.assertIn('LOOP_GUARD=stop', resume.stdout)
        self.assertEqual(self.read()['eligible_actions'], ['review'])
        self.command('next', 'Correct the failed comparison')
        self.command('select-function', 'demo-001')
        self.assertEqual(self.read()['status'], 'in_progress')

    def test_dependency_order_cycles_unknown_dependencies_and_multiple_active(self):
        caller = self.slice('caller', order=1, dependencies=[{'id': 'callee', 'blocking': True, 'required_status': 'verified'}])
        callee = self.slice('callee', order=2)
        self.write('plans/units/demo-slices.yaml', {'unit': 'demo', 'slices': [caller, callee]})
        self.command('reconcile')
        self.assertEqual(self.read()['current_unit'], 'callee')
        callee['dependencies'] = [{'id': 'caller'}]
        self.write('plans/units/demo-slices.yaml', {'unit': 'demo', 'slices': [caller, callee]})
        self.command('reconcile', success=False)
        callee['dependencies'] = [{'id': 'missing'}]
        self.write('plans/units/demo-slices.yaml', {'unit': 'demo', 'slices': [caller, callee]})
        self.command('reconcile', success=False)
        callee['dependencies'] = []
        caller['dependencies'] = []
        caller['status'] = callee['status'] = 'in_progress'
        self.write('plans/units/demo-slices.yaml', {'unit': 'demo', 'slices': [caller, callee]})
        self.command('reconcile', success=False)

    def test_draft_model_and_missing_map_never_skip_to_verification(self):
        self.ready()
        self.write('plans/units/demo-001-model.yaml', {'status': 'draft'})
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'modeling')
        self.write('plans/units/demo-001-model.yaml', {'status': 'confirmed'})
        (self.state / 'inventory/slices/demo-001/function-map.yaml').unlink()
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'inventory')

    def test_organize_derives_next_command_and_honors_binary_crate_path(self):
        self.ready()
        state = self.read()
        state['output']['kind'] = 'binary'
        state['target']['path'] = 'crates/parser'
        state['next_command'] = '.ctrust/sync.sh organize .'
        self.write('migration.yaml', state)
        self.command('organize')
        plan = self.read('plans/units/organization.yaml')
        self.assertEqual(plan['crate']['kind'], 'binary')
        self.assertEqual(plan['crate']['entry_file'], 'src/main.rs')
        self.assertEqual(plan['crate']['root'], str(self.project / 'crates/parser'))
        self.assertIn('select-function', self.read()['next_command'])
        self.command('validate-organization')
        plan['crate']['kind'] = 'library'
        self.write('plans/units/demo-001-organization.yaml', plan)
        result = self.command('validate-organization', success=False)
        self.assertIn('demo-001-organization.yaml', result.stdout)
        self.assertIn("crate.kind: expected 'binary', observed 'library'", result.stdout)

    def test_active_slice_gates_do_not_require_future_slice_artifacts(self):
        self.ready()
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'].append(self.slice('future'))
        self.write('plans/units/demo-slices.yaml', manifest)
        self.gate('behavior-gate.sh')
        self.gate('data-structure-gate.sh')
        self.gate('behavior-gate.sh', '--all', success=False)
        self.gate('behavior-gate.sh', 'future', success=False)

    def test_map_upgrade_is_explicit_preserves_source_and_populates_fields(self):
        self.write('inventory/slices/demo-001/function-map.yaml', {'version': 1, 'source': 'header_dependency_analyzer', 'functions': [{'c_name': 'foo', 'definition_file': 'foo.c', 'definition_line': 42, 'status': 'resolved'}]})
        self.command('function-map', 'demo-001')
        data = self.read('inventory/slices/demo-001/function-map.yaml')
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['functions'][0]['definition_line'], 42)
        self.assertEqual(data['functions'][0]['state'], 'planned')

    def test_installation_includes_controller_dependencies_and_templates(self):
        self.command('generic')
        self.assertFalse(list((self.state / 'tools').rglob('__pycache__')))
        for relative in ['tools/function_recovery.py', 'tools/workflow.py', 'tools/requirements.txt', 'templates/Cargo.workspace.toml', 'templates/test-evidence.ndjson']:
            self.assertTrue((self.state / relative).exists(), relative)
        self.controller = self.state / 'sync.sh'
        self.command('reconcile')
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('record-evidence', unit, self.evidence(unit))
        self.command('compare', unit, 'passed')
        result = subprocess.run([str(self.state / 'scripts/function-recovery-gate.sh'), str(self.project)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_artifact_reconciliation_preserves_text_handles_all_plans_and_is_idempotent(self):
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'].append(self.slice('future'))
        self.write('plans/units/demo-slices.yaml', manifest)
        self.command('plan-slices')
        path = self.state / 'plans/units/demo-001.md'
        path.write_text('Manual notes\n')
        before = (self.state / 'migration.yaml').read_bytes()
        self.command('reconcile-artifacts')
        self.assertTrue(path.read_text().startswith('Manual notes\n'))
        self.assertIn('ctrust:managed', (self.state / 'plans/units/future.md').read_text())
        self.assertEqual((self.state / 'migration.yaml').read_bytes(), before)
        first = path.read_bytes()
        self.command('reconcile-artifacts')
        self.assertEqual(path.read_bytes(), first)
        self.command('validate-artifacts')

    def test_legacy_upgrade_preserves_complete_nested_source_and_target(self):
        source = {'project_root': '/legacy', 'headers': ['api.h', 'types.h'], 'functions': [{'c_name': 'foo'}], 'custom': {'include_dirs': ['inc']}}
        target = {'project_root': str(self.project), 'path': 'crates/parser', 'custom': ['keep']}
        self.write('migration.yaml', {'source': source, 'target': target, 'output': {'kind': 'binary', 'binary_name': 'parser'}, 'status': 'scoped'})
        self.command('upgrade-state')
        self.assertEqual(self.read()['source'], source)
        self.assertEqual(self.read()['target']['custom'], ['keep'])
        self.assertEqual(self.read()['output']['binary_name'], 'parser')
        backup = self.read()['schema_upgrade_backup']
        self.assertEqual(self.read(backup)['source'], source)

    def test_verify_slice_runs_cargo_and_checkpoints_before_advancing(self):
        self.ready(names=('foo',))
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'].append(self.slice('future', order=2))
        self.write('plans/units/demo-slices.yaml', manifest)
        self.command('organize')
        (self.project / 'Cargo.toml').write_text('[package]\nname = "ctrust-regression"\nversion = "0.1.0"\nedition = "2021"\n[workspace]\n')
        result = subprocess.run(['cargo', 'fmt', '--all'], cwd=self.project, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        self.command('record-evidence', unit, self.evidence(unit, parity=False))
        self.command('compare', unit, 'passed')
        self.command('verify-slice', 'demo-001')
        self.assertEqual(self.read('plans/units/demo-slices.yaml')['slices'][0]['status'], 'verified')
        self.assertEqual(self.read()['current_unit'], 'future')
        self.assertEqual(self.read()['next_function'], 'none')
        self.assertEqual(self.read()['active_rust_file'], '')
        self.assertTrue((self.state / self.read()['last_checkpoint']).exists())

    def test_context_sync_does_not_grow_or_delete_manual_notes(self):
        self.command('status-slices')
        path = self.state / 'current-context.md'
        path.write_text('Manual decision: keep this.\n' + path.read_text())
        before = path.read_bytes()
        self.command('status-slices')
        self.command('status-slices')
        self.assertEqual(path.read_bytes(), before)

    def test_contract_and_model_template_commands_clear_their_next_command(self):
        self.ready(names=('foo',))
        (self.state / 'contracts/units/demo-001.yaml').unlink()
        (self.state / 'plans/units/demo-001-model.yaml').unlink()
        self.command('reconcile')
        self.assertIn('contract', self.read()['next_command'])
        self.command('contract', 'demo-001')
        self.assertEqual(self.read()['next_command'], '')
        self.assertEqual(self.read()['next_skill'], 'contract-extraction')
        self.write('contracts/units/demo-001.yaml', {'status': 'confirmed'})
        self.command('reconcile')
        self.assertIn('model', self.read()['next_command'])
        self.command('model', 'demo-001')
        self.assertEqual(self.read()['next_command'], '')
        self.assertEqual(self.read()['next_skill'], 'rust-domain-modeling')
        model = self.read('plans/units/demo-001-model.yaml')
        self.assertEqual(model['status'], 'draft')
        self.assertEqual(model['organization']['slice'], 'demo-001')
        self.assertEqual(model['source']['data_structures'], 'inventory/slices/demo-001/data-structures.yaml')

    def test_plans_and_status_accept_standard_yaml_list_indentation(self):
        self.command('plan-slices')
        self.assertTrue((self.state / 'plans/units/demo-001.md').exists())
        self.command('status-slices')
        report = (self.state / 'status/slices.md').read_text()
        self.assertIn('| demo-001 | planned | yes |', report)
        self.assertIn('| 1 | 1 | 0 | 0 | 0 | 0 |', report)

    def test_origin_check_does_not_accept_function_name_prefix(self):
        self.ready(names=('foo',))
        self.command('select-function', 'demo-001')
        unit = self.read()['active_unit']
        self.implementation(unit)
        path = self.project / self.rust_file
        path.write_text(path.read_text().replace('symbol=foo ', 'symbol=foobar '))
        self.gate('migration-completeness.sh', success=False)

    def test_duplicate_yaml_keys_rejected(self):
        path = self.state / 'plans/units/demo-slices.yaml'
        path.write_text('unit: demo\nunit: duplicate\nslices: []\n')
        self.command('reconcile', success=False)
        self.command('validate-artifacts', success=False)

    def test_quoted_and_flow_yaml_are_accepted_by_contract_and_model(self):
        self.ready(names=('foo',))
        for relative in ['inventory/slices/demo-001/data-structures.yaml',
                         'inventory/slices/demo-001/mapping-confirmation.yaml',
                         'inventory/slices/demo-001/behavior.yaml',
                         'contracts/units/demo-001.yaml']:
            data = self.read(relative)
            (self.state / relative).write_text(yaml.safe_dump(data, default_style='"', default_flow_style=True))
        self.command('contract', 'demo-001')
        (self.state / 'plans/units/demo-001-model.yaml').unlink()
        self.command('model', 'demo-001')
        self.assertEqual(self.read('plans/units/demo-001-model.yaml')['status'], 'draft')

    def test_structure_diagnostics_identify_typo_false_and_string_boolean(self):
        self.ready(names=('foo',))
        relative = 'inventory/slices/demo-001/data-structures.yaml'
        data = self.read(relative)
        data['completion']['structs_identified'] = data['completion'].pop('structures_identified')
        data['completion']['layout_mapped'] = False
        data['completion']['fields_mapped'] = 'true'
        self.write(relative, data)
        result = self.command('model', 'demo-001', success=False)
        self.assertIn(relative, result.stdout)
        self.assertIn('completion.structures_identified', result.stdout)
        self.assertIn('found structs_identified', result.stdout)
        self.assertIn('completion.layout_mapped', result.stdout)
        self.assertIn("observed 'true'", result.stdout)

    def test_incomplete_inventory_returns_to_inventory_and_blocks_advancement(self):
        self.ready(names=('foo',))
        for name in ['callers.yaml', 'dependencies.yaml', 'files.yaml']:
            (self.state / 'inventory/slices/demo-001' / name).unlink()
        self.command('reconcile')
        state = self.read()
        self.assertEqual(state['phase'], 'inventory')
        self.assertEqual(state['next_skill'], 'c-code-inventory')
        for name in ['callers.yaml', 'dependencies.yaml', 'files.yaml']:
            self.assertIn(name, state['next_action'])
        for command in ['contract', 'model', 'select-function', 'organize']:
            self.command(command, 'demo-001', success=False)
        self.gate('behavior-gate.sh', success=False)
        # Updating a folded diagnostic must preserve valid YAML and retry state.
        self.command('failed', 'Inventory incomplete: review missing files')
        self.assertEqual(self.read()['attempts'], 1)
        self.assertEqual(self.read()['last_error'], 'Inventory incomplete: review missing files')

    def test_inventory_validation_rejects_wrong_slice_and_invalid_artifacts(self):
        self.ready(names=('foo',))
        self.write('inventory/slices/demo-001/dependencies.yaml', {'slice': 'other', 'edges': 'invalid'})
        (self.state / 'inventory/slices/demo-001/files.yaml').write_text('files: []\nfiles: []\nheaders: []\n')
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'inventory')
        result = self.command('model', 'demo-001', success=False)
        self.assertIn("slice expected 'demo-001', observed 'other'", result.stdout)
        self.assertIn('edges expected list', result.stdout)
        self.assertIn('Duplicate YAML key', result.stdout)

    def test_mapping_diagnostic_rejects_boolean_unresolved_count(self):
        self.ready(names=('foo',))
        self.write('inventory/slices/demo-001/mapping-confirmation.yaml', {'status': 'confirmed', 'counts': {'unresolved': False}})
        result = self.command('model', 'demo-001', success=False)
        self.assertIn('counts.unresolved expected integer 0', result.stdout)

    def test_every_missing_inventory_artifact_prevents_phase_advancement(self):
        self.ready(names=('foo',))
        for name in INVENTORY_FIELDS:
            with self.subTest(artifact=name):
                path = self.state / 'inventory/slices/demo-001' / name
                original = path.read_bytes()
                path.unlink()
                self.command('reconcile')
                self.assertEqual(self.read()['phase'], 'inventory')
                self.assertIn(name, self.read()['next_action'])
                path.write_bytes(original)
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'function_recovery')

    def test_inventory_gate_accepts_drafts_but_confirmation_still_blocks(self):
        self.ready(names=('foo',))
        relative = 'inventory/slices/demo-001/data-structures.yaml'
        data = self.read(relative)
        data['status'] = 'draft'
        data['completion']['structures_identified'] = False
        self.write(relative, data)
        self.gate('inventory-gate.sh')
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'data_structure_mapping')
        self.assertIn('completion.structures_identified', self.read()['next_action'])
        self.command('model', 'demo-001', success=False)

    def legacy_source(self, declared_name='foo'):
        legacy = self.project / 'legacy with spaces'
        legacy.mkdir(exist_ok=True)
        (legacy / 'api.h').write_text(f'unsigned int {declared_name}(unsigned int value);\n')
        (legacy / 'api.c').write_text(f'#include "api.h"\nunsigned int {declared_name}(unsigned int value) {{ return value + 1; }}\n')
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'][0]['source'].update(project_root=str(legacy), headers=['api.h'])
        self.write('plans/units/demo-slices.yaml', manifest)
        return legacy

    def test_stage_machine_rejects_skips_even_when_phase_is_spoofed(self):
        self.ready(names=('foo',))
        self.write('plans/units/demo-001-model.yaml', {'status': 'draft'})
        state = self.read()
        state.update(phase='function_implementation', next_command='cargo test')
        self.write('migration.yaml', state)
        for command in ['organize', 'select-function']:
            result = self.command(command, 'demo-001', success=False)
            self.assertIn('required stage modeling', result.stdout)
        self.command('reconcile')
        self.assertEqual(self.read()['phase'], 'modeling')

    def test_stage_machine_rejects_ineligible_slice_and_preserves_blockers(self):
        self.ready(names=('foo',))
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'].append(self.slice('future', order=2))
        self.write('plans/units/demo-slices.yaml', manifest)
        self.command('model', 'future', success=False)
        self.assertFalse((self.state / 'plans/units/future-model.yaml').exists())
        self.command('failed', 'root cause not fixed')
        self.command('failed', 'root cause not fixed', success=False)
        for command in ['contract', 'model', 'organize', 'recover']:
            args = () if command == 'recover' else ('demo-001',)
            self.command(command, *args, success=False)
        self.assertEqual(self.read()['attempts'], 2)
        self.assertEqual(self.read()['status'], 'blocked')

    def test_stale_organization_has_executable_recovery_and_preserves_slice_notes(self):
        self.ready(names=('foo',))
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'][0]['semantic_name'] = 'digit_count'
        self.write('plans/units/demo-slices.yaml', manifest)
        notes = self.write('plans/units/demo-001.md', {'manual_decision': 'keep this'})
        before_notes = notes.read_bytes()
        self.command('reconcile')
        recovery = self.read()['recovery']
        self.assertEqual(recovery['code'], 'organization_stale')
        self.assertTrue(recovery['automatic'])
        self.assertIn('organize', self.read()['next_command'])
        self.command('failed', 'old organization validation failed')
        self.command('recover')
        self.command('validate-organization')
        self.assertEqual(self.read()['phase'], 'function_recovery')
        self.assertEqual(self.read()['attempts'], 0)
        self.assertEqual(notes.read_bytes(), before_notes)

    def test_recovery_exports_missing_inventory_from_declared_scope(self):
        self.legacy_source()
        self.command('reconcile')
        self.assertEqual(self.read()['recovery']['action'], 'inventory')
        self.command('recover')
        self.gate('inventory-gate.sh')
        self.assertEqual(self.read()['phase'], 'data_structure_mapping')
        self.assertIsNotNone(self.read('inventory/slices/demo-001/manifest.yaml')['generation'])
        self.assertEqual(self.read('inventory/slices/demo-001/function-map.yaml')['functions'][0]['c_name'], 'foo')

    def test_recovery_does_not_overwrite_confirmed_partial_inventory(self):
        self.ready(names=('foo',))
        self.legacy_source()
        behavior = self.state / 'inventory/slices/demo-001/behavior.yaml'
        before = behavior.read_bytes()
        (self.state / 'inventory/slices/demo-001/callers.yaml').unlink()
        self.command('recover')
        self.assertEqual(self.read()['recovery']['code'], 'inventory_review_required')
        self.assertFalse(self.read()['recovery']['automatic'])
        self.assertEqual(behavior.read_bytes(), before)
        self.assertEqual(self.read()['attempts'], 0)

    def test_recovery_requires_changed_inputs_after_execution_failure(self):
        self.legacy_source(declared_name='other')
        self.command('recover', success=False)
        self.assertEqual(self.read()['attempts'], 1)
        self.assertEqual(self.read()['failure_kind'], 'execution')
        result = self.command('recover', success=False)
        self.assertIn('unchanged inputs', result.stdout)
        self.assertEqual(self.read()['attempts'], 1)
        self.legacy_source(declared_name='foo')
        self.command('recover')
        self.assertEqual(self.read()['attempts'], 0)
        self.gate('inventory-gate.sh')

    def test_analysis_pending_recovery_does_not_count_as_execution_failure(self):
        self.ready(names=('foo',))
        path = 'inventory/slices/demo-001/behavior.yaml'
        data = self.read(path)
        data['status'] = 'draft'
        self.write(path, data)
        self.command('recover')
        self.assertEqual(self.read()['phase'], 'behavior_extraction')
        self.assertEqual(self.read()['recovery']['code'], 'behavior_unconfirmed')
        self.assertEqual(self.read()['attempts'], 0)
        resume = self.command('resume', '--brief')
        self.assertIn('RECOVERY_CODE=behavior_unconfirmed', resume.stdout)
        self.assertIn('ELIGIBLE_ACTIONS=', resume.stdout)
        self.assertIn('using NEXT_SKILL', resume.stdout)

    def test_invalid_organization_inputs_require_review_without_retrying(self):
        self.ready(names=('foo',))
        manifest = self.read('plans/units/demo-slices.yaml')
        manifest['slices'][0]['target'] = {'output_kind': 'invalid'}
        self.write('plans/units/demo-slices.yaml', manifest)
        self.command('recover')
        self.assertEqual(self.read()['recovery']['code'], 'organization_inputs_invalid')
        self.assertFalse(self.read()['recovery']['automatic'])
        self.assertEqual(self.read()['attempts'], 0)

    def test_direct_inventory_execution_records_failure_and_blocks_unchanged_retries(self):
        self.legacy_source(declared_name='other')
        self.command('inventory', 'demo-001', success=False)
        self.assertEqual(self.read()['attempts'], 1)
        self.command('recover', success=False)
        self.assertEqual(self.read()['attempts'], 1)
        self.legacy_source(declared_name='foo')
        self.command('inventory', 'demo-001')
        self.assertEqual(self.read()['attempts'], 0)
        self.assertTrue(self.read()['recovery_history'])


if __name__ == '__main__':
    unittest.main()
