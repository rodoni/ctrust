import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyze
from export_inventory import compact_behavior_function, compact_structures, export_inventory, merge_analyses


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def project(self, name, header, sources):
        root = self.root / name
        root.mkdir()
        (root / 'api.h').write_text(header, encoding='utf-8')
        for filename, content in sources.items():
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return root

    def test_cache_preserves_identity_of_identical_files_and_projects(self):
        code = 'void root(void) {}'
        root = self.project('first', 'void root(void);', {'a.c': code, 'b.c': code})
        cache = self.root / 'cache'
        for _ in range(2):
            _, report = analyze.analyze(root, root / 'api.h', cache_dir=cache)
            self.assertEqual(report['roots'][0]['candidates'], ['a.c:1:root', 'b.c:1:root'])
        other = self.project('second', 'void root(void);', {'a.c': code})
        graph, _ = analyze.analyze(other, other / 'api.h', cache_dir=cache)
        self.assertIn('a.c:1:root', graph)
        with patch.object(analyze, 'parse_file_bundle', side_effect=AssertionError('cache miss')):
            cached = analyze.parse_file_cached(other / 'a.c', cache)
        self.assertEqual(cached[0][0]['file'], str(other / 'a.c'))

    def test_cache_invalidates_content_and_analyzer_version(self):
        root = self.project('source', 'void root(void);', {'a.c': 'void root(void) {}'})
        cache = self.root / 'cache'
        analyze.parse_file_cached(root / 'a.c', cache)
        (root / 'a.c').write_text('void changed(void) {}')
        self.assertEqual(analyze.parse_file_cached(root / 'a.c', cache)[0][0]['name'], 'changed')
        with patch.object(analyze, 'ANALYZER_VERSION', 'next'), patch.object(analyze, 'parse_file_bundle', wraps=analyze.parse_file_bundle) as parse:
            analyze.parse_file_cached(root / 'a.c', cache)
            parse.assert_called_once()

    def test_corrupted_cache_is_rebuilt(self):
        root = self.project('source', 'void root(void);', {'a.c': 'void root(void) {}'})
        cache = self.root / 'cache'
        analyze.parse_file_cached(root / 'a.c', cache)
        next(cache.glob('*.json')).write_text('{')
        self.assertEqual(analyze.parse_file_cached(root / 'a.c', cache)[0][0]['name'], 'root')

    def test_valid_json_cache_corruption_is_rebuilt(self):
        root = self.project('source', 'void root(void);', {'a.c': 'void root(void) {}'})
        cache = self.root / 'cache'
        analyze.parse_file_cached(root / 'a.c', cache)
        path = next(cache.glob('*.json'))
        envelope = json.loads(path.read_text())
        envelope['bundle']['definitions'][0]['name'] = 'wrong'
        path.write_text(json.dumps(envelope))
        self.assertEqual(analyze.parse_file_cached(root / 'a.c', cache)[0][0]['name'], 'root')
        path.write_text('[]')
        self.assertEqual(analyze.parse_file_cached(root / 'a.c', cache)[0][0]['name'], 'root')

    def test_missing_include_is_reported_and_architecture_editor_can_be_exported(self):
        root = self.project('source', 'void root(void);', {'a.c': '#include "missing.h"\nvoid root(void) {}'})
        graph, report = analyze.analyze(root, root / 'api.h')
        self.assertEqual(report['unresolved_includes'][0]['include'], '"missing.h"')
        output = self.root / 'analysis'
        result = subprocess.run([sys.executable, str(Path(analyze.__file__)), '--project', str(root), '--header', str(root / 'api.h'), '--output', str(output), '--architecture'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Exportar plano JSON', (output / 'architecture.html').read_text())

    def test_parameter_and_local_callbacks_do_not_resolve_to_global_functions(self):
        root = self.project('source', 'void root(void (*cb)(void));', {
            'a.c': 'void cb(void) {}\nvoid root(void (*cb)(void)) { cb(); { void (*local)(void) = cb; local(); } }',
        })
        graph, report = analyze.analyze(root, root / 'api.h')
        self.assertEqual({item['expression'] for item in report['indirect_calls']}, {'cb', 'local'})
        self.assertTrue(all(item['candidates'] == [] for item in report['indirect_calls']))
        self.assertNotIn('a.c:1:cb', graph)
        self.assertEqual(len(graph.edges), 0)

    def test_block_and_for_scopes_restore_function_bindings(self):
        root = self.project('source', 'void root(void);', {
            'a.c': 'void cb(void) {}\nvoid root(void) { { void (*cb)(void); cb(); } for (void (*cb)(void) = 0; cb; ) cb(); cb(); }',
        })
        graph, report = analyze.analyze(root, root / 'api.h')
        self.assertEqual(len(report['indirect_calls']), 2)
        self.assertTrue(graph.has_edge('a.c:2:root', 'a.c:1:cb'))

    def test_global_callback_from_included_header_is_indirect(self):
        root = self.project('source', 'extern void (*cb)(void);\nvoid root(void);', {
            'a.c': '#include "api.h"\nvoid cb(void) {}\nvoid root(void) { cb(); }',
        })
        graph, report = analyze.analyze(root, root / 'api.h')
        self.assertEqual(len(report['indirect_calls']), 1)
        self.assertEqual(report['indirect_calls'][0]['candidates'], [])
        self.assertEqual(len(graph.edges), 0)

    def test_missing_requested_function_blocks_before_publication(self):
        root = self.project('source', 'void root(void);', {'a.c': 'void root(void) {}'})
        output = self.root / 'inventory'
        with self.assertRaisesRegex(ValueError, 'typo'):
            export_inventory(root, [root / 'api.h'], output, root_functions=['root', 'typo'])
        self.assertFalse(output.exists())

    def test_selection_spans_multiple_headers(self):
        root = self.project('source', 'void first(void);', {
            'other.h': 'void second(void);', 'a.c': 'void first(void) {}\nvoid second(void) {}',
        })
        graph, report = merge_analyses(root, [root / 'api.h', root / 'other.h'], None, root_functions=['first', 'second'])
        self.assertEqual({item['name'] for item in report['roots']}, {'first', 'second'})
        self.assertEqual(len(graph), 2)

    def test_cli_without_function_exports_all_header_functions(self):
        root = self.project('source', 'void first(void); void second(void);', {
            'a.c': 'void first(void) {}\nvoid second(void) {}',
        })
        output = self.root / 'inventory'
        command = [sys.executable, str(Path(analyze.__file__).with_name('export_inventory.py')), '--project', str(root), '--header', str(root / 'api.h'), '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        from migration.store import read_yaml
        self.assertEqual({item['label'] for item in read_yaml(output)['functions'].values()}, {'first', 'second'})

    def test_unresolved_and_ambiguous_roots_are_exported_without_progress(self):
        root = self.project('source', 'void missing(void); void duplicate(void);', {
            'a.c': 'void duplicate(void) {}', 'b.c': 'void duplicate(void) {}',
        })
        output = self.root / 'inventory'
        export_inventory(root, [root / 'api.h'], output)
        from migration.store import read_yaml
        functions = read_yaml(output)['functions'].values()
        self.assertEqual({(item['label'], item['status']) for item in functions}, {('missing', 'unresolved'), ('duplicate', 'ambiguous')})
        self.assertTrue(all('state' not in item for item in read_yaml(output)['functions'].values()))

    def test_typedef_alias_and_body_types_reach_nested_structures(self):
        root = self.project('source',
            'struct Child { int value; };\n'
            'typedef struct { struct Child *child; } Item;\n'
            'typedef Item Alias;\nstruct Unused { int ignored; };\nvoid root(void);',
            {'a.c': '#include "api.h"\nvoid root(void) { Alias local; local.child = 0; }'})
        graph, report = analyze.analyze(root, root / 'api.h')
        compact = compact_structures(report['structures'], graph)
        self.assertEqual({item['name'] for item in compact}, {'Item', 'Child'})
        self.assertEqual(len(compact), 2)

    def test_tag_alias_forward_declarations_do_not_duplicate_definitions(self):
        root = self.project('source', 'struct Item; typedef struct Item Alias; struct Item { int value; }; void root(Alias *p);',
            {'a.c': '#include "api.h"\nvoid root(Alias *p) { p->value = 1; }'})
        graph, report = analyze.analyze(root, root / 'api.h')
        compact = compact_structures(report['structures'], graph)
        self.assertEqual(len(compact), 1)
        self.assertEqual(compact[0]['fields'][0]['name'], 'value')

    def test_pointer_array_multiple_and_callback_fields_keep_names(self):
        root = self.project('source', 'struct Item { int *p; int a[4]; int x, y; void (*cb)(void); }; void root(struct Item *p);',
            {'a.c': '#include "api.h"\nvoid root(struct Item *p) {}'})
        graph, report = analyze.analyze(root, root / 'api.h')
        structures = compact_structures(report['structures'], graph)
        fields = {item['name']: item for item in structures[0]['fields']}
        self.assertEqual(set(fields), {'p', 'a', 'x', 'y', 'cb'})
        self.assertEqual(fields['p']['pointer_depth'], 1)
        self.assertEqual(fields['cb']['pointer_depth'], 1)
        self.assertEqual(fields['a']['pointer_depth'], 0)

    def test_same_named_structures_keep_separate_identities(self):
        root = self.project('source', 'void first(void); void second(void);', {
            'a.c': 'struct Item { int x; }; void first(void) { struct Item local; }',
            'b.c': 'struct Item { int y; }; void second(void) { struct Item local; }',
        })
        graph, report = analyze.analyze(root, root / 'api.h')
        compact = compact_structures(report['structures'], graph)
        self.assertEqual(len(compact), 2)
        self.assertEqual(len({item['id'] for item in compact}), 2)
        self.assertEqual({item['fields'][0]['name'] for item in compact}, {'x', 'y'})

    def test_global_effects_exclude_locals_and_distinguish_reads_from_writes(self):
        root = self.project('source', 'void root(int x);', {
            'a.c': 'int counter, source, target;\nvoid root(int x) { int local = x; target = local; counter += source; counter++; { int source = 1; local += source; } for (int counter = 0; counter < 2; counter++) {} }',
        })
        graph, _ = analyze.analyze(root, root / 'api.h')
        attrs = graph.nodes['a.c:2:root']
        self.assertEqual([item['name'] for item in attrs['globals_read']], ['counter', 'source', 'counter'])
        self.assertEqual([item['name'] for item in attrs['globals_written']], ['target', 'counter', 'counter'])

    def test_header_globals_and_pointer_memory_writes_are_classified(self):
        root = self.project('source', 'extern int counter; extern int *pointer; void root(void);', {
            'a.c': '#include "api.h"\nvoid root(void) { counter = 1; *pointer = counter; }',
        })
        graph, _ = analyze.analyze(root, root / 'api.h')
        attrs = graph.nodes['a.c:2:root']
        self.assertEqual({item['name'] for item in attrs['globals_read']}, {'counter', 'pointer'})
        self.assertEqual([item['name'] for item in attrs['globals_written']], ['counter'])

    def test_global_array_writes_and_block_extern_objects(self):
        root = self.project('source', 'void root(void);', {
            'a.c': 'int array[2]; int *pointer;\nvoid root(void) { extern int external; array[0] = 1; array[1]++; pointer[0] = external; external = 2; }',
        })
        graph, _ = analyze.analyze(root, root / 'api.h')
        attrs = graph.nodes['a.c:2:root']
        self.assertEqual([item['name'] for item in attrs['globals_read']], ['array', 'pointer', 'external'])
        self.assertEqual([item['name'] for item in attrs['globals_written']], ['array', 'array', 'external'])

    def test_anonymous_nested_and_body_structures_are_reachable(self):
        root = self.project('source', 'struct Item { struct { int value; } nested; }; void root(struct Item *p);', {
            'a.c': '#include "api.h"\nvoid root(struct Item *p) { struct { int local; } variable; }',
        })
        graph, report = analyze.analyze(root, root / 'api.h')
        structures = compact_structures(report['structures'], graph)
        self.assertEqual(len(structures), 3)
        self.assertEqual({field['name'] for item in structures for field in item['fields']}, {'nested', 'value', 'local'})

    def test_indirect_calls_preserve_uncertainty_and_candidate_evidence(self):
        root = self.project('source', 'void root(void);', {
            'a.c': 'void process(void) {}\nvoid root(void) { object.process(); }',
        })
        graph, report = merge_analyses(root, [root / 'api.h'], None)
        self.assertTrue(report['indirect_calls'])
        node = report['roots'][0]['definition']
        behavior = compact_behavior_function(graph, node, graph.nodes[node])
        details = behavior['indirect_calls'][0]
        self.assertEqual(details['confidence'], 'inferred')
        self.assertEqual(details['candidates'], ['a.c:1:process'])
        self.assertTrue(details['source'].endswith('a.c:2'))

    def test_inline_only_header_and_inline_callee_are_roots(self):
        root = self.project('source', 'static inline void leaf(void) {}\nstatic inline void root(void) { leaf(); }', {})
        graph, report = analyze.analyze(root, root / 'api.h', root_functions=['root'])
        self.assertEqual(report['roots'][0]['status'], 'resolved')
        self.assertTrue(graph.has_edge('api.h:2:root', 'api.h:1:leaf'))

    def test_static_header_functions_are_instantiated_per_translation_unit(self):
        root = self.project('source', 'static inline void helper(void) { local(); }\nvoid first(void); void second(void);', {
            'a.c': '#include "api.h"\nstatic void local(void) {}\nvoid first(void) { helper(); }',
            'b.c': '#include "api.h"\nstatic void local(void) {}\nvoid second(void) { helper(); }',
        })
        graph, report = analyze.analyze(root, root / 'api.h', root_functions=['first', 'second'])
        self.assertFalse(report['unresolved_calls'])
        self.assertTrue(graph.has_edge('api.h:1:helper@a.c', 'a.c:2:local'))
        self.assertTrue(graph.has_edge('api.h:1:helper@b.c', 'b.c:2:local'))
        self.assertFalse(graph.has_edge('api.h:1:helper@a.c', 'b.c:2:local'))

    def test_nested_includes_follow_compile_database_search_paths(self):
        root = self.project('source', 'void root(void);', {
            'headers/wrapper.h': '#include <helpers.h>',
            'headers/helpers.h': 'static inline void helper(void) {}',
            'a.c': '#include "wrapper.h"\nvoid root(void) { helper(); }',
            'unused.h': 'static inline void helper(void) {}',
        })
        database = root / 'compile_commands.json'
        database.write_text(json.dumps([{'directory': str(root), 'file': 'a.c', 'arguments': ['cc', '-I', 'headers', '-c', 'a.c']}]))
        graph, report = analyze.analyze(root, root / 'api.h', database)
        self.assertFalse(report['unresolved_calls'])
        self.assertFalse(report['ambiguous_calls'])
        self.assertIn('headers/helpers.h:1:helper@a.c', graph)

    def test_forced_includes_search_include_directories(self):
        root = self.project('source', 'void root(void);', {
            'headers/helpers.h': 'static inline void helper(void) {}',
            'a.c': 'void root(void) { helper(); }',
        })
        database = root / 'compile_commands.json'
        database.write_text(json.dumps([{'directory': str(root), 'file': 'a.c', 'arguments': ['cc', '-Iheaders', '-include', 'helpers.h', '-c', 'a.c']}]))
        graph, report = analyze.analyze(root, root / 'api.h', database)
        self.assertFalse(report['unresolved_calls'])
        self.assertIn('headers/helpers.h:1:helper@a.c', graph)


if __name__ == '__main__':
    unittest.main()
