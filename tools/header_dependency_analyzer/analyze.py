"""Header-driven approximate C call graph, without building the project."""
import argparse
import hashlib
import html
import json
import re
import shlex
import copy
import os
import tempfile
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx
from tree_sitter import Language, Parser
import tree_sitter_c

LANG = Language(tree_sitter_c.language())
SOURCE_SUFFIXES = {'.c'}
HEADER_SUFFIXES = {'.h'}
ANALYZABLE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES
ANALYZER_VERSION = '3'


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
    if node.type in ('identifier', 'field_identifier', 'type_identifier'):
        return text(node, source)
    if 'declarator' in node.type:
        child = node.child_by_field_name('declarator')
        if child is None:
            child = next((c for c in node.named_children if 'declarator' in c.type or c.type in ('identifier', 'field_identifier', 'type_identifier')), None)
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
                if name and fd.child_by_field_name('declarator').type != 'parenthesized_declarator':
                    result.append(name)
    return result


def pointer_depth(node):
    return sum(child.type == 'pointer_declarator' for child in walk(node)) if node else 0


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
                for child in field.children_by_field_name('declarator'):
                    fields.append({
                    'name': name_from_declarator(child, source) or '',
                    'declaration': raw_type,
                    'pointer_depth': pointer_depth(child),
                    'source': location(field, path),
                })
        if body is None:
            continue
        aliases = []
        if node.parent and node.parent.type == 'type_definition':
            aliases = [name_from_declarator(c, source) for c in node.parent.children_by_field_name('declarator')]
        identity = f'{path}:{node.start_byte}'
        structures.append({
            'id': identity,
            'name': name or next((a for a in aliases if a), '<anonymous>'),
            'aliases': [a for a in aliases if a],
            'kind': node.type.replace('_specifier', ''),
            'declaration': location(node, path),
            'fields': fields,
            'raw': text(node, source)[:1000],
            'start_byte': node.start_byte,
            'end_byte': node.end_byte,
        })
    return structures


def object_declarations(node, source):
    """Names and storage kinds, excluding function prototypes."""
    result = {}
    for decl in node.children_by_field_name('declarator'):
        actual = decl.child_by_field_name('declarator') if decl.type == 'init_declarator' else decl
        fd = find_function_declarator(actual)
        if fd and fd.child_by_field_name('declarator').type != 'parenthesized_declarator':
            continue
        name = name_from_declarator(actual, source)
        if name:
            result[name] = 'callback' if fd else 'pointer' if pointer_depth(actual) else 'object'
    return result


def extract_function_details(node, source, path, global_objects=None):
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
    global_objects = dict(global_objects or {})
    scopes = [{}]
    parameters = fd.child_by_field_name('parameters')
    if parameters:
        for param in parameters.named_children:
            scopes[0].update(object_declarations(param, source))

    def binding(value):
        for scope in reversed(scopes):
            if value in scope:
                return scope[value]
        return None

    def effect(child, mode):
        if child is None:
            return
        if child.type == 'identifier':
            value = text(child, source)
            if binding(value) == 'global' or (binding(value) is None and value in global_objects):
                entry = {'name': value, 'source': location(child, path)}
                if mode in ('read', 'both'):
                    globals_read.append(entry)
                if mode in ('write', 'both'):
                    globals_written.append(entry)
            return
        if child.type == 'subscript_expression':
            argument = child.child_by_field_name('argument')
            value = text(argument, source) if argument else ''
            # Array storage can be written; pointer storage is only read here.
            effect(argument, mode if global_objects.get(value) == 'array' else 'read')
            effect(child.child_by_field_name('index'), 'read')
            return
        for descendant in child.named_children:
            effect(descendant, 'read')

    def visit(child):
        if child.type in ('compound_statement', 'for_statement'):
            if child.type == 'for_statement':
                control_flow.append({'kind': child.type, 'source': location(child, path), 'text': text(child, source)[:400]})
            scopes.append({})
            for descendant in child.named_children:
                visit(descendant)
            scopes.pop()
            return
        if child.type == 'declaration':
            names = object_declarations(child, source)
            extern = any(c.type == 'storage_class_specifier' and text(c, source) == 'extern' for c in child.named_children)
            scopes[-1].update({key: 'global' if extern else kind for key, kind in names.items()})
            for decl in child.children_by_field_name('declarator'):
                if decl.type == 'init_declarator':
                    value = decl.child_by_field_name('value')
                    if value:
                        visit(value)
            return
        if child.type == 'call_expression':
            fn = child.child_by_field_name('function')
            expression = text(fn, source) if fn else ''
            shadowed = fn is not None and fn.type == 'identifier' and (binding(expression) is not None or expression in global_objects)
            if fn is not None and fn.type == 'identifier' and not shadowed:
                calls.append(expression)
            elif fn is not None:
                indirect.append(expression)
                indirect_details.append({'expression': expression, 'shadowed': shadowed, **location(child, path)})
                effect(fn, 'read')
            arguments = child.child_by_field_name('arguments')
            if arguments:
                for argument in arguments.named_children:
                    visit(argument)
            return
        if child.type in ('assignment_expression', 'update_expression'):
            left = child.child_by_field_name('left') if child.type == 'assignment_expression' else child.child_by_field_name('argument')
            operator = child.child_by_field_name('operator')
            mode = 'write' if child.type == 'assignment_expression' and operator and text(operator, source) == '=' else 'both'
            if left:
                accesses.append({'expression': text(left, source), 'source': location(child, path), 'kind': 'write'})
                effect(left, mode)
            right = child.child_by_field_name('right')
            if right:
                visit(right)
            return
        if child.type in ('if_statement', 'switch_statement', 'case_statement', 'while_statement', 'do_statement', 'return_statement', 'goto_statement'):
            control_flow.append({'kind': child.type, 'source': location(child, path), 'text': text(child, source)[:400]})
        if child.type == 'field_expression':
            accesses.append({'expression': text(child, source), 'source': location(child, path), 'kind': 'field'})
        if child.type == 'identifier':
            effect(child, 'read')
            return
        for descendant in child.named_children:
            visit(descendant)

    if body:
        visit(body)
    prefix = text(node, source).split('{', 1)[0]
    is_static = bool(re.search(r'\bstatic\b', prefix))
    return {
        'name': name,
        'signature': prefix,
        'file': str(path),
        'line': node.start_point.row + 1,
        'static': is_static,
        'body_text': text(node, source),
        'source_hash': hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest(),
        'start_byte': node.start_byte,
        'end_byte': node.end_byte,
        'calls': sorted(set(calls)),
        'indirect_calls': indirect,
        'indirect_call_details': indirect_details,
        'control_flow': control_flow,
        'accesses': accesses,
        'globals_read': globals_read,
        'globals_written': globals_written,
    }


def parse_file_bundle(path):
    source = path.read_bytes()
    try:
        tree = Parser(LANG).parse(source)
    except ValueError as exc:
        if 'Incompatible Language version' in str(exc):
            raise RuntimeError('Incompatible tree-sitter packages. Reinstall dependencies with `python3 -m pip install -r requirements.txt`.') from exc
        raise
    definitions = []
    declarations = []
    objects = {}
    aliases = {}
    includes = []
    for node in walk(tree.root_node):
        if node.type == 'preproc_include':
            include = node.child_by_field_name('path')
            if include:
                includes.append(text(include, source))
        if node.type == 'type_definition':
            type_node = node.child_by_field_name('type')
            if type_node:
                for alias in node.children_by_field_name('declarator'):
                    alias_name = name_from_declarator(alias, source)
                    if alias_name:
                        aliases[alias_name] = text(type_node, source)
        if node.type == 'declaration' and not any(p.type == 'function_definition' for p in ancestors(node)):
            objects.update(object_declarations(node, source))
            for decl in node.children_by_field_name('declarator'):
                if decl.type == 'array_declarator':
                    objects[name_from_declarator(decl, source)] = 'array'
    for node in walk(tree.root_node):
        if node.type == 'function_definition':
            detail = extract_function_details(node, source, path, objects)
            if detail:
                definitions.append(detail)
                if path.suffix == '.h':
                    declarations.append(detail['name'])
        elif node.type == 'declaration':
            declarations.extend(declaration_names(node, source))
    return {'definitions': definitions, 'declarations': sorted(set(declarations)), 'structures': extract_structures(tree, source, path), 'objects': objects, 'aliases': aliases, 'includes': includes}


def ancestors(node):
    while node.parent:
        node = node.parent
        yield node


def parse_file_details(path):
    bundle = parse_file_bundle(path)
    return bundle['definitions'], bundle['declarations'], bundle['structures']


def parse_file(path):
    definitions, declarations, _ = parse_file_details(path)
    return definitions, declarations


def cached_bundle(path, cache_dir=None):
    path = Path(path).resolve()
    if cache_dir is None:
        return parse_file_bundle(path)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(path).encode() + ANALYZER_VERSION.encode() + path.read_bytes()).hexdigest()
    cache_file = cache_dir / f'{digest}.json'
    if cache_file.is_file():
        try:
            envelope = json.loads(cache_file.read_text(encoding='utf-8'))
            result = envelope.get('bundle') if isinstance(envelope, dict) else None
            checksum = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            if isinstance(envelope, dict) and envelope.get('key') == digest and envelope.get('checksum') == checksum and isinstance(result, dict) and all(isinstance(result.get(k), list) for k in ('definitions', 'declarations', 'structures', 'includes')) and all(isinstance(result.get(k), dict) for k in ('objects', 'aliases')):
                return result
        except (ValueError, UnicodeError):
            pass
    result = parse_file_bundle(path)
    fd, temporary = tempfile.mkstemp(dir=cache_dir, prefix='.parse-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'key': digest, 'bundle': result, 'checksum': hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()}, handle, ensure_ascii=False)
        os.replace(temporary, cache_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result


def parse_file_cached(path, cache_dir=None):
    bundle = cached_bundle(path, cache_dir)
    return copy.deepcopy((bundle['definitions'], bundle['declarations'], bundle['structures']))


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


def include_paths(entry, project):
    directories, forced = [project], []
    if not entry:
        return directories, forced
    arguments = iter(entry['arguments'])
    for argument in arguments:
        if argument in ('-I', '-iquote', '-isystem'):
            directories.append(_resolve_entry_path(next(arguments, ''), Path(entry['directory'])))
        elif argument.startswith('-I'):
            directories.append(_resolve_entry_path(argument[2:], Path(entry['directory'])))
        elif argument == '-include':
            forced.append(next(arguments, ''))
    return directories, forced


def included_files(path, bundles, directories, forced=(), missing=None):
    found = set()

    def follow(current, extra=()):
        if current in found or current not in bundles:
            return
        found.add(current)
        for include in [*bundles[current]['includes'], *extra]:
            name = include.strip('"<>')
            search = ([current.parent] if not include.startswith('<') else []) + directories
            target = next((candidate for directory in search if (candidate := (directory / name).resolve()) in bundles), None)
            if target:
                follow(target)
            elif missing is not None:
                missing.append({'file': str(current), 'include': include, 'searched': [str(directory) for directory in search]})
    follow(path, forced)
    return found


def analyze(project, header, compile_commands=None, cache_dir=None, root_functions=None):
    project = Path(project).expanduser().resolve()
    header = Path(header).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f'Project directory does not exist: {project}')
    if not header.is_file() or not header.is_relative_to(project):
        raise ValueError('Header must exist inside the project directory')
    compile_database = load_compile_commands(compile_commands, project) if compile_commands is not None else None
    all_files, compile_database = _project_files(project, compile_database)
    bundles = {path: cached_bundle(path, cache_dir) for path in all_files}
    header_roots = bundles[header]['declarations']
    structures = [copy.deepcopy(item) for bundle in bundles.values() for item in bundle['structures']]
    for item in structures:
        item['id'] = str(Path(item['declaration']['file']).relative_to(project)) + ':' + str(item['start_byte'])
    entries = {Path(e['file']): e for e in (compile_database or {}).get('selected_sources', [])}
    contexts = {}
    missing_includes = []
    definitions = []
    for path in all_files:
        directories, forced = include_paths(entries.get(path), project)
        visible = included_files(path, bundles, directories, forced, missing_includes)
        contexts[path] = visible
        if path.suffix == '.h':
            visible = {path}
        objects = {name: kind for p in sorted(visible) for name, kind in bundles[p]['objects'].items()}
        for included in sorted(visible):
            if included.suffix == '.c' and included != path:
                continue
            data = included.read_bytes()
            tree = Parser(LANG).parse(data)
            for node in walk(tree.root_node):
                if node.type != 'function_definition':
                    continue
                detail = extract_function_details(node, data, included, objects)
                if not detail:
                    continue
                if included.suffix == '.h' and path.suffix == '.c' and detail['static']:
                    detail['translation_unit'] = str(path.relative_to(project))
                elif included != path:
                    continue
                detail['visible_files'] = [str(p) for p in visible]
                definitions.append(detail)
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
        if definition.get('translation_unit'):
            definition['id'] += '@' + definition['translation_unit']
        by_name[definition['name']].append(definition)
    aliases = {name: target for bundle in bundles.values() for name, target in bundle['aliases'].items()}
    for definition in definitions:
        body = definition['body_text']
        # Expand typedef references conservatively, including types used in bodies.
        for _ in range(len(aliases) + 1):
            expanded = body
            for alias, target in aliases.items():
                if re.search(rf'\b{re.escape(alias)}\b', body) and target not in expanded:
                    expanded += ' ' + target
            if expanded == body:
                break
            body = expanded
        visible = set(definition['visible_files'])
        definition['type_references'] = sorted(item['id'] for item in structures if (
            item['declaration']['file'] in visible and (
                (item['name'] != '<anonymous>' and re.search(rf'\b{re.escape(item["name"])}\b', body)) or
                (item['declaration']['file'] == str(project / definition['file']) and definition['start_byte'] <= item['start_byte'] < definition['end_byte'])
            )
        ))

    graph = nx.DiGraph()
    unresolved = []
    ambiguous = []
    indirect = []
    visited = set()

    def resolve(name, caller=None):
        candidates = by_name.get(name, [])
        if caller:
            context = caller.get('translation_unit', caller['file'])
            candidates = [item for item in candidates if not item['static'] or item.get('translation_unit', item['file']) == context]
            local = [item for item in candidates if item['static'] and item.get('translation_unit', item['file']) == context]
            if local:
                candidates = local
        else:
            candidates = [item for item in candidates if not item['static'] or (item['file'] == str(header.relative_to(project)) and not item.get('translation_unit'))]
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
        graph.add_node(definition['id'], label=definition['name'], file=definition['file'], line=definition['line'], status='resolved', static=definition['static'], translation_unit=definition.get('translation_unit', ''), source_hash=definition['source_hash'], **{key: definition.get(key, []) for key in ('control_flow', 'accesses', 'globals_read', 'globals_written', 'indirect_call_details', 'type_references')})
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
            candidates, confidence = ([], 'unresolved') if item.get('shadowed') else indirect_candidates(item['expression'], definition)
            detail = {'caller': definition['id'], 'expression': item['expression'], 'source': dict(item), 'confidence': confidence, 'candidates': candidates}
            indirect.append(detail)
            item.update(confidence=confidence, candidates=candidates, source=dict(item))

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
    relevant = {str(header)} | {str(project / attrs['file']) for _, attrs in graph.nodes(data=True) if attrs.get('file')}
    for path in list(relevant):
        relevant.update(str(value) for value in contexts.get(Path(path), set()))
    report['unresolved_includes'] = list({(item['file'], item['include']): {**item, 'file': str(Path(item['file']).relative_to(project))} for item in missing_includes if item['file'] in relevant}.values())
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
    parser.add_argument('--architecture', action='store_true', help='Also export the standalone schema-3 architecture editor')
    args = parser.parse_args()
    graph, report = analyze(args.project, args.header, args.compile_commands, args.cache_dir)
    write_results(graph, report, args.output)
    if args.architecture:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from migration.inventory import collect
        from migration.html import write_report
        inventory = collect(args.project, [args.header], compile_commands=args.compile_commands, cache=args.cache_dir)
        state = {'version': 3, 'revision': 0, 'source': {'root': str(args.project.resolve())}, 'inventory': inventory, 'architecture': {}, 'progress': {}, 'slice_results': {}, 'closure': None, 'issues': {}, 'history': []}
        write_report(args.output, state, args.output / 'architecture.html')
    print(f"Indexed {report['files_indexed']} files; {report['definitions_indexed']} definitions")
    print(f"Roots: {len(report['roots'])}; graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
    print(f"Unresolved: {len(report['unresolved_calls'])}; ambiguous: {len(report['ambiguous_calls'])}; indirect: {len(report['indirect_calls'])}")
    print('Output:', args.output.resolve())


if __name__ == '__main__':
    main()
