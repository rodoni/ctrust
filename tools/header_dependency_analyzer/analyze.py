"""Header-driven approximate C call graph, without building the project."""
import argparse
import hashlib
import html
import json
import re
import shlex
from collections import defaultdict
from pathlib import Path

import networkx as nx
from tree_sitter import Language, Parser
import tree_sitter_c

LANG = Language(tree_sitter_c.language())
SOURCE_SUFFIXES = {'.c'}
HEADER_SUFFIXES = {'.h'}
ANALYZABLE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES
ANALYZER_VERSION = '2'


def text(node, source):
    return source[node.start_byte:node.end_byte].decode('utf-8', errors='replace')


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def location(node, path):
    return {'file': str(path), 'line': node.start_point.row + 1, 'column': node.start_point.column + 1}


def name_from_declarator(node, source):
    if node is None:
        return None
    if node.type == 'identifier':
        return text(node, source)
    if node.type == 'function_declarator':
        child = node.child_by_field_name('declarator')
        if child is None or child.type == 'parenthesized_declarator':
            return None
        return name_from_declarator(child, source)
    if node.type in ('pointer_declarator', 'attributed_declarator', 'parenthesized_declarator'):
        child = node.child_by_field_name('declarator')
        if child is None:
            child = next((c for c in node.named_children if 'declarator' in c.type), None)
        return name_from_declarator(child, source)
    return None


def find_function_declarator(node):
    if node is None:
        return None
    if node.type == 'function_declarator':
        return node
    for child in node.named_children:
        result = find_function_declarator(child)
        if result is not None:
            return result
    return None


def declaration_names(node, source):
    if node.type != 'declaration':
        return []
    result = []
    for child in node.named_children:
        if child.type in ('function_declarator', 'pointer_declarator', 'attributed_declarator', 'init_declarator'):
            decl = child.child_by_field_name('declarator') if child.type == 'init_declarator' else child
            fd = find_function_declarator(decl)
            if fd is not None:
                name = name_from_declarator(fd, source)
                if name:
                    result.append(name)
    return result


def pointer_depth(node):
    depth = 0
    while node is not None:
        if node.type == 'pointer_declarator':
            depth += 1
        node = node.child_by_field_name('declarator')
    return depth


def extract_structures(tree, source, path):
    structures = []
    for node in walk(tree.root_node):
        if node.type not in ('struct_specifier', 'union_specifier', 'enum_specifier'):
            continue
        name_node = node.child_by_field_name('name')
        name = text(name_node, source) if name_node is not None else ''
        body = node.child_by_field_name('body')
        fields = []
        if body is not None:
            for field in body.named_children:
                if field.type not in ('field_declaration', 'struct_declaration'):
                    continue
                raw_type = text(field, source)
                field_names = []
                for child in field.named_children:
                    if child.type in ('field_identifier', 'identifier'):
                        field_names.append(text(child, source))
                    elif 'declarator' in child.type:
                        field_name = name_from_declarator(child, source)
                        if field_name:
                            field_names.append(field_name)
                fields.append({
                    'name': field_names[0] if field_names else '',
                    'declaration': raw_type,
                    'pointer_depth': max((pointer_depth(c) for c in field.named_children if 'declarator' in c.type), default=0),
                    'source': location(field, path),
                })
        structures.append({
            'name': name or '<anonymous>',
            'kind': node.type.replace('_specifier', ''),
            'declaration': location(node, path),
            'fields': fields,
            'raw': text(node, source)[:1000],
        })
    return structures


def extract_function_details(node, source, path):
    decl = node.child_by_field_name('declarator')
    fd = find_function_declarator(decl)
    name = name_from_declarator(fd, source)
    if not name:
        return None
    body = node.child_by_field_name('body')
    calls = []
    indirect = []
    indirect_details = []
    control_flow = []
    accesses = []
    globals_read = []
    globals_written = []
    if body:
        call_nodes = set()
        for child in walk(body):
            if child.type == 'call_expression':
                call_nodes.add(id(child))
                fn = child.child_by_field_name('function')
                if fn is not None and fn.type == 'identifier':
                    calls.append(text(fn, source))
                elif fn is not None:
                    indirect.append(text(fn, source))
                    indirect_details.append({'expression': text(fn, source), **location(child, path)})
            if child.type in ('if_statement', 'switch_statement', 'case_statement', 'for_statement', 'while_statement', 'do_statement', 'return_statement', 'goto_statement'):
                control_flow.append({'kind': child.type, 'source': location(child, path), 'text': text(child, source)[:400]})
            if child.type == 'field_expression':
                accesses.append({'expression': text(child, source), 'source': location(child, path), 'kind': 'field'})
            if child.type == 'assignment_expression':
                left = child.child_by_field_name('left')
                if left is not None:
                    accesses.append({'expression': text(left, source), 'source': location(child, path), 'kind': 'write'})
            if child.type == 'identifier' and id(child) not in call_nodes:
                value = text(child, source)
                if value not in {name, 'if', 'else', 'return', 'sizeof'} and value not in calls:
                    globals_read.append({'name': value, 'source': location(child, path)})
    prefix = text(node, source).split('{', 1)[0]
    is_static = bool(re.search(r'\bstatic\b', prefix))
    return {
        'name': name,
        'signature': prefix,
        'file': str(path),
        'line': node.start_point.row + 1,
        'static': is_static,
        'calls': sorted(set(calls)),
        'indirect_calls': indirect,
        'indirect_call_details': indirect_details,
        'control_flow': control_flow,
        'accesses': accesses,
        'globals_read': globals_read,
        'globals_written': globals_written,
    }


def parse_file_details(path):
    source = path.read_bytes()
    try:
        tree = Parser(LANG).parse(source)
    except ValueError as exc:
        if 'Incompatible Language version' in str(exc):
            raise RuntimeError('Incompatible tree-sitter packages. Reinstall dependencies with `python3 -m pip install -r requirements.txt`.') from exc
        raise
    definitions = []
    declarations = []
    for node in walk(tree.root_node):
        if node.type == 'function_definition':
            detail = extract_function_details(node, source, path)
            if detail:
                definitions.append(detail)
        elif node.type == 'declaration':
            declarations.extend(declaration_names(node, source))
    return definitions, sorted(set(declarations)), extract_structures(tree, source, path)


def parse_file(path):
    definitions, declarations, _ = parse_file_details(path)
    return definitions, declarations


def parse_file_cached(path, cache_dir=None):
    if cache_dir is None:
        return parse_file_details(path)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_file = cache_dir / f'{digest}.json'
    if cache_file.is_file():
        return json.loads(cache_file.read_text(encoding='utf-8'))
    result = parse_file_details(path)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def _resolve_entry_path(value, directory):
    path = Path(value).expanduser()
    return (directory / path if not path.is_absolute() else path).resolve()


def load_compile_commands(database_path, project):
    database_path = Path(database_path).expanduser().resolve()
    if not database_path.is_file():
        raise ValueError(f'Compilation database does not exist: {database_path}')
    try:
        data = json.loads(database_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid compilation database JSON: {database_path}') from exc
    if not isinstance(data, list):
        raise ValueError('Compilation database must contain a JSON array')
    entries = []
    selected_sources = {}
    for index, raw in enumerate(data):
        if not isinstance(raw, dict) or not raw.get('file') or not raw.get('directory'):
            raise ValueError(f'Compilation database entry {index} requires file and directory')
        if 'arguments' in raw and 'command' in raw:
            raise ValueError(f'Compilation database entry {index} cannot contain both arguments and command')
        if 'arguments' in raw:
            arguments = raw['arguments']
            if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
                raise ValueError(f'Compilation database entry {index} arguments must be a string array')
        elif 'command' in raw:
            try:
                arguments = shlex.split(raw['command'])
            except ValueError as exc:
                raise ValueError(f'Invalid command in compilation database entry {index}') from exc
        else:
            raise ValueError(f'Compilation database entry {index} requires arguments or command')
        directory = _resolve_entry_path(raw['directory'], Path.cwd())
        source = _resolve_entry_path(raw['file'], directory)
        entry = {'file': str(source), 'directory': str(directory), 'arguments': arguments}
        entries.append(entry)
        if source.suffix.lower() in SOURCE_SUFFIXES and source.is_file() and source.is_relative_to(project):
            selected_sources[str(source)] = entry
    return {'path': database_path, 'entries': entries, 'selected_sources': list(selected_sources.values())}


def _project_files(project, compile_database=None):
    all_files = sorted(path for path in project.rglob('*') if path.suffix.lower() in ANALYZABLE_SUFFIXES and path.is_file())
    if compile_database is None:
        return all_files, None
    selected = {Path(entry['file']) for entry in compile_database['selected_sources']}
    headers = {path for path in all_files if path.suffix.lower() in HEADER_SUFFIXES}
    return sorted(headers | selected), compile_database


def analyze(project, header, compile_commands=None, cache_dir=None, root_functions=None):
    project = Path(project).expanduser().resolve()
    header = Path(header).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f'Project directory does not exist: {project}')
    if not header.is_file() or not header.is_relative_to(project):
        raise ValueError('Header must exist inside the project directory')
    compile_database = load_compile_commands(compile_commands, project) if compile_commands is not None else None
    all_files, compile_database = _project_files(project, compile_database)
    definitions = []
    header_roots = []
    structures = []
    for path in all_files:
        defs, decls, structs = parse_file_cached(path, cache_dir)
        definitions.extend(defs)
        structures.extend(structs)
        if path == header:
            header_roots = decls
    if not header_roots:
        raise ValueError('No function declarations found in target header')
    if root_functions is not None:
        requested = list(dict.fromkeys(root_functions))
        missing = [name for name in requested if name not in header_roots]
        if missing:
            raise ValueError(f'Requested functions are not declared in target header {header}: {", ".join(missing)}')
        header_roots = requested
    by_name = defaultdict(list)
    for definition in definitions:
        definition['file'] = str(Path(definition['file']).relative_to(project))
        definition['id'] = f"{definition['file']}:{definition['line']}:{definition['name']}"
        by_name[definition['name']].append(definition)
    structure_names = {item.get('name') for item in structures if item.get('name') and item.get('name') != '<anonymous>'}
    for definition in definitions:
        signature = definition.get('signature', '')
        definition['type_references'] = sorted(name for name in structure_names if re.search(rf'\b{re.escape(name)}\b', signature))

    graph = nx.DiGraph()
    unresolved = []
    ambiguous = []
    indirect = []
    visited = set()

    def resolve(name, caller=None):
        candidates = by_name.get(name, [])
        if caller:
            candidates = [item for item in candidates if not item['static'] or item['file'] == caller['file']]
            local = [item for item in candidates if item['static'] and item['file'] == caller['file']]
            if local:
                candidates = local
        else:
            candidates = [item for item in candidates if not item['static']]
        return candidates[0] if len(candidates) == 1 else candidates

    def indirect_candidates(expression, caller):
        field = re.split(r'[.>]', expression)[-1]
        candidates = by_name.get(field, [])
        if caller:
            candidates = [item for item in candidates if not item['static'] or item['file'] == caller['file']]
        if len(candidates) == 1:
            return [candidates[0]['id']], 'inferred'
        if len(candidates) > 1:
            return [item['id'] for item in candidates], 'ambiguous'
        return [], 'unresolved'

    def traverse(definition):
        if definition['id'] in visited:
            return
        visited.add(definition['id'])
        graph.add_node(definition['id'], label=definition['name'], file=definition['file'], line=definition['line'], status='resolved', **{key: definition.get(key, []) for key in ('control_flow', 'accesses', 'globals_read', 'globals_written', 'indirect_call_details', 'type_references')})
        for call in sorted(set(definition['calls'])):
            target = resolve(call, definition)
            if isinstance(target, dict):
                graph.add_node(target['id'], label=target['name'], file=target['file'], line=target['line'], status='resolved')
                graph.add_edge(definition['id'], target['id'], relation='calls', confidence='confirmed', source='direct_identifier')
                traverse(target)
            else:
                status = 'ambiguous' if target else 'unresolved'
                ident = f'{status}:{definition["id"]}:{call}'
                graph.add_node(ident, label=call, file='', line=0, status=status)
                graph.add_edge(definition['id'], ident, relation='calls', confidence=status, source='direct_identifier')
                (ambiguous if target else unresolved).append({'caller': definition['id'], 'name': call, 'candidates': [item['id'] for item in target]})
        for item in definition['indirect_call_details']:
            candidates, confidence = indirect_candidates(item['expression'], definition)
            indirect.append({'caller': definition['id'], 'expression': item['expression'], 'source': item, 'confidence': confidence, 'candidates': candidates})

    root_info = []
    for name in header_roots:
        target = resolve(name)
        if isinstance(target, dict):
            root_info.append({'name': name, 'definition': target['id'], 'status': 'resolved', 'confidence': 'confirmed'})
            traverse(target)
        else:
            status = 'ambiguous' if target else 'unresolved'
            root_info.append({'name': name, 'definition': None, 'status': status, 'confidence': status, 'candidates': [item['id'] for item in target]})
    cycles = [sorted(component) for component in nx.strongly_connected_components(graph) if len(component) > 1]
    report = {'analyzer_version': ANALYZER_VERSION, 'project': str(project), 'header': str(header.relative_to(project)), 'roots': root_info, 'unresolved_calls': unresolved, 'ambiguous_calls': ambiguous, 'indirect_calls': indirect, 'definitions_indexed': len(definitions), 'files_indexed': len(all_files), 'structures': structures, 'cycles': cycles}
    if compile_database is not None:
        report.update({'compile_commands': str(compile_database['path']), 'compile_commands_entries': len(compile_database['entries']), 'translation_units_selected': len(compile_database['selected_sources'])})
    return graph, report


def tree_lines(graph, node, prefix='', ancestors=None):
    ancestors = ancestors or set()
    data = graph.nodes[node]
    label = data['label'] + '()' + (f' [{data["file"]}:{data["line"]}]' if data['file'] else f' [{data["status"]}]')
    if node in ancestors:
        return [prefix + label + ' [cycle]']
    lines = [prefix + label]
    for child in sorted(graph.successors(node), key=lambda item: graph.nodes[item]['label']):
        lines.extend(tree_lines(graph, child, prefix + '    ', ancestors | {node}))
    return lines


def write_results(graph, report, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'functions.json').write_text(json.dumps([{'id': node, **attrs} for node, attrs in graph.nodes(data=True)], indent=2, ensure_ascii=False), encoding='utf8')
    (out / 'dependencies.json').write_text(json.dumps({**report, 'edges': [{'caller': a, 'callee': b, **attrs} for a, b, attrs in graph.edges(data=True)]}, indent=2, ensure_ascii=False), encoding='utf8')
    lines = []
    for root in report['roots']:
        if root['definition']:
            lines.extend(tree_lines(graph, root['definition']))
        else:
            lines.append(root['name'] + '() [' + root['status'] + ']')
        lines.append('')
    (out / 'hierarchy.txt').write_text('\n'.join(lines), encoding='utf8')
    (out / 'hierarchy.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Header dependencies</title><pre>' + html.escape('\n'.join(lines)) + '</pre>',
        encoding='utf8',
    )
    graphml = nx.DiGraph()
    for node, attrs in graph.nodes(data=True):
        graphml.add_node(node, **{key: (json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value) for key, value in attrs.items()})
    for caller, callee, attrs in graph.edges(data=True):
        graphml.add_edge(caller, callee, **attrs)
    nx.write_graphml(graphml, out / 'graph.graphml')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--header', required=True, type=Path)
    parser.add_argument('--compile-commands', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--output', default='analysis', type=Path)
    args = parser.parse_args()
    graph, report = analyze(args.project, args.header, args.compile_commands, args.cache_dir)
    write_results(graph, report, args.output)
    print(f"Indexed {report['files_indexed']} files; {report['definitions_indexed']} definitions")
    print(f"Roots: {len(report['roots'])}; graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
    print(f"Unresolved: {len(report['unresolved_calls'])}; ambiguous: {len(report['ambiguous_calls'])}; indirect: {len(report['indirect_calls'])}")
    print('Output:', args.output.resolve())


if __name__ == '__main__':
    main()
