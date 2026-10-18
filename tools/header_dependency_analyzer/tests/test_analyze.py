import json
import tempfile
import unittest
from pathlib import Path

import networkx as nx

import analyze


class AnalyzerTests(unittest.TestCase):
    def make_project(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / 'include').mkdir()
        (root / 'src').mkdir()
        return temp, root

    def test_parse_file_extracts_direct_and_indirect_calls(self):
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        source_path = root / 'src' / 'calls.c'
        source_path.write_text(
            'void callback(void);\n'
            'void run(void) { callback(); object.handler(); }\n',
            encoding='utf-8',
        )

        definitions, declarations = analyze.parse_file(source_path)

        self.assertEqual(declarations, ['callback'])
        self.assertEqual(definitions[0]['name'], 'run')
        self.assertEqual(definitions[0]['calls'], ['callback'])
        self.assertEqual(definitions[0]['indirect_calls'], ['object.handler'])
        self.assertFalse(definitions[0]['static'])

    def test_resolves_transitive_dependencies_and_unresolved_calls(self):
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        (root / 'include' / 'api.h').write_text(
            'void root(void);\n', encoding='utf-8'
        )
        (root / 'src' / 'api.c').write_text(
            'void leaf(void) {}\n'
            'void root(void) { leaf(); missing(); }\n', encoding='utf-8'
        )

        graph, report = analyze.analyze(root, root / 'include' / 'api.h')

        self.assertEqual(report['roots'][0]['status'], 'resolved')
        self.assertIn('src/api.c:2:root', graph)
        self.assertIn('src/api.c:1:leaf', graph)
        self.assertTrue(graph.has_edge('src/api.c:2:root', 'src/api.c:1:leaf'))
        self.assertEqual(report['unresolved_calls'][0]['name'], 'missing')

    def test_static_resolution_is_file_local_and_duplicate_globals_are_ambiguous(self):
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        (root / 'include' / 'api.h').write_text(
            'void root(void);\n', encoding='utf-8'
        )
        (root / 'src' / 'a.c').write_text(
            'static void helper(void) {}\n'
            'void root(void) { helper(); shared(); }\n', encoding='utf-8'
        )
        (root / 'src' / 'b.c').write_text(
            'void shared(void) {}\n', encoding='utf-8'
        )
        (root / 'src' / 'c.c').write_text(
            'void shared(void) {}\n', encoding='utf-8'
        )

        graph, report = analyze.analyze(root, root / 'include' / 'api.h')

        self.assertIn('src/a.c:1:helper', graph)
        self.assertEqual(len(report['ambiguous_calls']), 1)
        self.assertEqual(report['ambiguous_calls'][0]['name'], 'shared')
        self.assertNotIn('src/b.c:1:shared', graph)

    def test_exported_map_uses_recovery_schema_and_preserves_function_identity(self):
        from export_inventory import export_slice
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        header = root / 'include/api.h'
        header.write_text('void root(void);\n')
        (root / 'src/api.c').write_text('void leaf(void) {}\nvoid root(void) { leaf(); missing(); }\n')
        output = root / 'inventory'
        export_slice(root, [header], output, 'demo-001')
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workflow import load
        data = load(output / 'function-map.yaml')
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['slice'], 'demo-001')
        self.assertEqual(len({fn['function_id'] for fn in data['functions']}), len(data['functions']))
        by_name = {fn['c_name']: fn for fn in data['functions']}
        self.assertEqual(by_name['root']['state'], 'planned')
        self.assertEqual(by_name['missing']['state'], 'blocked')
        self.assertEqual(by_name['root']['evidence_refs'], [])

    def test_behavior_export_is_scoped_to_roots_and_preserves_transitive_dependencies(self):
        from export_inventory import export_slice
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        header = root / 'include/api.h'
        header.write_text('void root(void);\n')
        (root / 'src/api.c').write_text('void leaf(void) {}\nvoid root(void) { leaf(); }\n')
        output = root / 'inventory'
        export_slice(root, [header], output, 'demo-001', root_functions=['root'])
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workflow import load
        behavior = load(output / 'behavior.yaml')
        self.assertEqual([item['c_name'] for item in behavior['functions']], ['root'])
        self.assertTrue((output / 'behavior/functions/root.yaml').is_file())
        self.assertEqual(behavior['functions'][0]['artifact'], 'behavior/functions/root.yaml')
        self.assertEqual(behavior['scope']['reachable_functions'], ['src/api.c:1:leaf', 'src/api.c:2:root'])
        self.assertEqual(behavior['references'][0]['c_name'], 'leaf')
        dependencies = load(output / 'dependencies.yaml')
        self.assertEqual(len(dependencies['edges']), 1)

        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        header = root / 'include' / 'api.h'
        header.write_text('void root(void);\n', encoding='utf-8')
        selected = root / 'src' / 'selected.c'
        selected.write_text(
            'void leaf(void) {}\nvoid root(void) { leaf(); }\n', encoding='utf-8'
        )
        ignored = root / 'src' / 'ignored.c'
        ignored.write_text('void ignored(void) {}\n', encoding='utf-8')
        database = root / 'compile_commands.json'
        database.write_text(json.dumps([
            {
                'directory': str(root),
                'file': 'src/selected.c',
                'command': 'cc -I include -D FEATURE=1 -c src/selected.c',
            },
            {
                'directory': str(root),
                'file': 'README.txt',
                'arguments': ['cc', '-c', 'README.txt'],
            },
        ]), encoding='utf-8')

        graph, report = analyze.analyze(root, header, database)

        self.assertEqual(report['compile_commands_entries'], 2)
        self.assertEqual(report['translation_units_selected'], 1)
        self.assertEqual(report['files_indexed'], 2)
        self.assertIn('src/selected.c:2:root', graph)
        self.assertNotIn('src/ignored.c:1:ignored', graph)

    def test_compile_commands_arguments_and_invalid_json(self):
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        header = root / 'include' / 'api.h'
        header.write_text('void root(void);\n', encoding='utf-8')
        source = root / 'src' / 'root.c'
        source.write_text('void root(void) {}\n', encoding='utf-8')
        database = root / 'compile_commands.json'
        database.write_text(json.dumps([{
            'directory': str(root),
            'file': 'src/root.c',
            'arguments': ['cc', '-DNAME=value', '-I', 'include', '-c', 'src/root.c'],
        }]), encoding='utf-8')
        loaded = analyze.load_compile_commands(database, root)
        self.assertEqual(loaded['selected_sources'][0]['arguments'][1], '-DNAME=value')

        database.write_text('{invalid', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Invalid compilation database JSON'):
            analyze.load_compile_commands(database, root)

    def test_indirect_call_candidates_are_reported_conservatively(self):
        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        (root / 'include' / 'api.h').write_text('void root(void);\n', encoding='utf-8')
        (root / 'src' / 'api.c').write_text(
            'void process(void) {}\n'
            'void root(void) { object.process(); }\n', encoding='utf-8'
        )
        graph, report = analyze.analyze(root, root / 'include' / 'api.h')
        self.assertEqual(report['indirect_calls'][0]['expression'], 'object.process')
        self.assertEqual(report['indirect_calls'][0]['confidence'], 'inferred')
        self.assertEqual(report['indirect_calls'][0]['candidates'], ['src/api.c:1:process'])

        temp, root = self.make_project()
        self.addCleanup(temp.cleanup)
        header = root / 'include' / 'api.h'
        header.write_text('void root(void);\n', encoding='utf-8')
        (root / 'src' / 'root.c').write_text('void root(void) {}\n', encoding='utf-8')
        graph, report = analyze.analyze(root, header)
        output = root / 'analysis'

        analyze.write_results(graph, report, output)

        expected = {'functions.json', 'dependencies.json', 'hierarchy.txt', 'graph.graphml', 'hierarchy.html'}
        self.assertEqual({path.name for path in output.iterdir()}, expected)
        dependencies = json.loads((output / 'dependencies.json').read_text(encoding='utf-8'))
        self.assertEqual(dependencies['roots'][0]['status'], 'resolved')
        graphml = nx.read_graphml(output / 'graph.graphml')
        self.assertEqual(len(graphml.nodes), 1)


if __name__ == '__main__':
    unittest.main()
