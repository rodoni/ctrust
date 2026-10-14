"""Header-driven approximate C call graph, without building the project."""
import argparse
import hashlib
import html
import json
import re
import shlex
import tempfile
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


def location_from_detail(detail):
    return {key: detail[key] for key in ('file', 'line', 'column')}


def name_from_declarator(node, source):
    if node is None:
        return None
    if node.type in ('identifier', 'field_identifier', 'type_identifier'):
        return text(node, source)
    if 'declarator' in node.type:
        child = node.child_by_field_name('declarator')
        if child is None:
            child = next((c for c in node.named_children if 'declarator' in c.type), None)
        return name_from_declarator(child, source)
    return None


def declarators(node):
    return node.children_by_field_name('declarator')


def is_function_declarator(node):
    """The first derived type outward from the name distinguishes f() from (*f)()."""
    chain = []
    while node is not None:
        if node.type in ('function_declarator', 'pointer_declarator', 'array_declarator'):
            chain.append(node.type)
        child = node.child_by_field_name('declarator')
        if child is None and node.type == 'parenthesized_declarator':
            child = next(iter(node.named_children), None)
        node = child
    return bool(chain) and chain[-1] == 'function_declarator'


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
    for decl in declarators(node):
        if is_function_declarator(decl):
            name = name_from_declarator(decl, source)
            if name:
                result.append(name)
    return result


def pointer_depth(node):
    depth = 0
    while node is not None:
        if node.type == 'pointer_declarator':
            depth += 1
        child = node.child_by_field_name('declarator')
        if child is None and node.type == 'parenthesized_declarator':
            child = next(iter(node.named_children), None)
        node = child
    return depth


def anonymous_type_name(node, path):
    return f'<anonymous@{path}:{node.start_byte}>'


def type_names(node, source, path=None):
    if node is None:
        return []
    names = {text(child, source) for child in walk(node) if child.type == 'type_identifier'}
    if path is not None:
        names.update(anonymous_type_name(child, path) for child in walk(node)
                     if child.type in ('struct_specifier', 'union_specifier', 'enum_specifier')
                     and child.child_by_field_name('name') is None
                     and child.child_by_field_name('body') is not None)
    return sorted(names)


def extract_structures(tree, source, path):
    structures = []
    for node in walk(tree.root_node):
        if node.type not in ('struct_specifier', 'union_specifier', 'enum_specifier'):
            continue
        name_node = node.child_by_field_name('name')
        name = text(name_node, source) if name_node is not None else ''
        body = node.child_by_field_name('body')
        if body is None:
            continue  # A tag reference or forward declaration is not a definition.
        parent = node.parent
        aliases = []
        if parent is not None and parent.type == 'type_definition':
            aliases = [name_from_declarator(child, source) for child in declarators(parent)]
            aliases = [alias for alias in aliases if alias]
        anonymous = anonymous_type_name(node, path) if not name else None
        name = name or (aliases[0] if aliases else anonymous)
        fields = []
        if body is not None:
            for field in body.named_children:
                if field.type not in ('field_declaration', 'struct_declaration'):
                    continue
                raw_type = text(field, source)
                for child in declarators(field) or [None]:
                    fields.append({
                        'name': name_from_declarator(child, source) if child is not None else '<anonymous>',
                        'declaration': raw_type,
                        'pointer_depth': pointer_depth(child),
                        'type_names': type_names(field.child_by_field_name('type'), source, path),
                        'source': location(child if child is not None else field, path),
                    })
        structures.append({
            'id': f'{path}:{node.start_point.row + 1}:{node.start_point.column + 1}',
            'name': name,
            'aliases': aliases,
            'anonymous_name': anonymous,
            'kind': node.type.replace('_specifier', ''),
            'declaration': location(node, path),
            'fields': fields,
            'raw': text(node, source)[:1000],
        })
    return structures


def global_effects(uses, objects, object_kinds=None):
    reads, writes = [], []
    for item in uses:
        if item['name'] not in objects and item.get('binding') != 'global':
            continue
        mode = item['mode']
        if mode.startswith('indexed_'):
            mode = mode.removeprefix('indexed_') if (object_kinds or {}).get(item['name']) == 'array' else 'read'
        effect = {'name': item['name'], 'source': item['source']}
        if mode in ('read', 'readwrite'):
            reads.append(effect)
        if mode in ('write', 'readwrite'):
            writes.append(effect)
    return reads, writes


def extract_function_details(node, source, path, global_objects=None, object_kinds=None):
    decl = node.child_by_field_name('declarator')
    fd = find_function_declarator(decl)
    name = name_from_declarator(decl, source)
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
    identifier_uses = []
    direct_call_details = []
    scopes = [{value: 'global' for value in (global_objects or [])}, {}]
    parameters = fd.child_by_field_name('parameters') if fd is not None else None
    if parameters is not None:
        for parameter in parameters.named_children:
            parameter_name = name_from_declarator(parameter.child_by_field_name('declarator'), source)
            if parameter_name:
                scopes[-1][parameter_name] = 'object'

    def binding(value):
        return next((scope[value] for scope in reversed(scopes) if value in scope), None)

    def inspect(child, mode='read'):
        if child.type in ('compound_statement', 'for_statement'):
            scopes.append({})
            inspect_children(child)
            scopes.pop()
            return
        if child.type == 'declaration':
            for declaration in declarators(child):
                value = name_from_declarator(declaration, source)
                if value:
                    is_extern = any(c.type == 'storage_class_specifier' and text(c, source) == 'extern' for c in child.named_children)
                    scopes[-1][value] = 'function' if is_function_declarator(declaration) else ('global' if is_extern else 'object')
                initializer = declaration.child_by_field_name('value')
                if initializer is not None:
                    inspect(initializer)
                # Array dimensions can read globals even though the declared name is local.
                for part in walk(declaration):
                    size = part.child_by_field_name('size')
                    if size is not None:
                        inspect(size)
            return
        if child.type == 'call_expression':
            fn = child.child_by_field_name('function')
            if fn is not None:
                expression = text(fn, source)
                bound = binding(expression) if fn.type == 'identifier' else None
                if fn.type == 'identifier' and bound not in ('object', 'global'):
                    calls.append(expression)
                    direct_call_details.append({'name': expression, **location(child, path)})
                else:
                    indirect.append(expression)
                    indirect_details.append({'expression': expression, 'binding': bound or 'expression', **location(child, path)})
                    inspect(fn)
            arguments = child.child_by_field_name('arguments')
            if arguments is not None:
                inspect(arguments)
            return
        if child.type == 'assignment_expression':
            left = child.child_by_field_name('left')
            right = child.child_by_field_name('right')
            operator = child.child_by_field_name('operator')
            if left is not None:
                accesses.append({'expression': text(left, source), 'source': location(child, path), 'kind': 'write'})
                inspect(left, 'write' if text(operator, source) == '=' else 'readwrite')
            if right is not None:
                inspect(right)
            return
        if child.type == 'update_expression':
            argument = child.child_by_field_name('argument')
            if argument is not None:
                accesses.append({'expression': text(argument, source), 'source': location(child, path), 'kind': 'write'})
                inspect(argument, 'readwrite')
            return
        if child.type == 'field_expression':
            accesses.append({'expression': text(child, source), 'source': location(child, path), 'kind': 'field'})
            argument = child.child_by_field_name('argument')
            operator = child.child_by_field_name('operator')
            if argument is not None:
                inspect(argument, 'read' if text(operator, source) == '->' else mode)
            return
        if child.type == 'subscript_expression':
            argument = child.child_by_field_name('argument')
            index = child.child_by_field_name('index')
            if argument is not None:
                inspect(argument, f'indexed_{mode}' if mode in ('write', 'readwrite') and argument.type == 'identifier' else 'read')
            if index is not None:
                inspect(index)
            return
        if child.type == 'pointer_expression':
            argument = child.child_by_field_name('argument')
            if argument is not None:
                inspect(argument, 'read')
            return
        if child.type == 'identifier':
            value = text(child, source)
            if binding(value) not in ('object', 'function'):
                identifier_uses.append({'name': value, 'mode': mode, 'binding': binding(value), 'source': location(child, path)})
            return
        if child.type == 'goto_statement':
            control_flow.append({'kind': child.type, 'source': location(child, path), 'text': text(child, source)[:400]})
            return
        if child.type == 'statement_identifier':
            return
        inspect_children(child)

    def inspect_children(child):
        if child.type in ('if_statement', 'switch_statement', 'case_statement', 'for_statement', 'while_statement', 'do_statement', 'return_statement', 'goto_statement'):
            control_flow.append({'kind': child.type, 'source': location(child, path), 'text': text(child, source)[:400]})
        for part in child.named_children:
            inspect(part)

    if body:
        inspect(body)
    globals_read, globals_written = global_effects(identifier_uses, global_objects or [], object_kinds)
    prefix = text(node, source).split('{', 1)[0]
    is_static = bool(re.search(r'\bstatic\b', prefix))
    return {
        'name': name,
        'signature': prefix,
        'file': str(path),
        'line': node.start_point.row + 1,
        'static': is_static,
        'calls': sorted(set(calls)),
        'direct_call_details': direct_call_details,
        'identifier_uses': identifier_uses,
        'type_names': type_names(node, source, path),
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
    objects = set()
    object_kinds = {}
    aliases = {}
    includes = []
    for node in walk(tree.root_node):
        parent = node.parent
        inside_function = False
        while parent is not None:
            if parent.type == 'function_definition':
                inside_function = True
                break
            parent = parent.parent
        if node.type == 'declaration' and not inside_function:
            objects.update(name_from_declarator(decl, source) for decl in declarators(node) if not is_function_declarator(decl))
            for decl in declarators(node):
                if is_function_declarator(decl):
                    continue
                value = name_from_declarator(decl, source)
                outer = decl.child_by_field_name('declarator') if decl.type == 'init_declarator' else decl
                if value and outer is not None:
                    object_kinds[value] = 'array' if outer.type == 'array_declarator' else 'object'
        if node.type == 'type_definition':
            target = node.child_by_field_name('type')
            if target is not None:
                for decl in declarators(node):
                    alias = name_from_declarator(decl, source)
                    if alias:
                        tag = target.child_by_field_name('name')
                        aliases[alias] = [text(tag, source)] if tag is not None else (type_names(target, source) if target.type == 'type_identifier' else [])
        if node.type == 'preproc_include':
            target = node.child_by_field_name('path')
            if target is not None and target.type in ('string_literal', 'system_lib_string'):
                includes.append(text(target, source))
    for node in walk(tree.root_node):
        if node.type == 'function_definition':
            detail = extract_function_details(node, source, path, objects, object_kinds)
            if detail:
                definitions.append(detail)
                if path.suffix.lower() in HEADER_SUFFIXES:
                    declarations.append(detail['name'])
        elif node.type == 'declaration' and node.parent is not None:
            parent = node.parent
            while parent is not None and parent.type != 'function_definition':
                parent = parent.parent
            if parent is not None:
                continue
            declarations.extend(declaration_names(node, source))
    return {'definitions': definitions, 'declarations': sorted(set(declarations)), 'structures': extract_structures(tree, source, path), 'objects': sorted(objects - {None}), 'object_kinds': object_kinds, 'aliases': aliases, 'includes': includes}


def parse_file_details(path):
    bundle = parse_file_bundle(path)
    return bundle['definitions'], bundle['declarations'], bundle['structures']


def parse_file(path):
    definitions, declarations, _ = parse_file_details(path)
    return definitions, declarations


def parse_file_cached(path, cache_dir=None, with_metadata=False):
    path = Path(path).resolve()
    if cache_dir is None:
        result = parse_file_bundle(path)
    else:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(json.dumps([ANALYZER_VERSION, str(path)]).encode() + path.read_bytes()).hexdigest()
        cache_file = cache_dir / f'{digest}.json'
        result = None
        if cache_file.is_file():
            try:
                result = json.loads(cache_file.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                pass
        if result is None:
            result = parse_file_bundle(path)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache_dir, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(result, handle, ensure_ascii=False)
            temporary.replace(cache_file)
    return result if with_metadata else (result['definitions'], result['declarations'], result['structures'])


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


def include_options(arguments, directory):
    directories, quoted, forced = [], [], []
    iterator = iter(arguments)
    for argument in iterator:
        for option, target in (('-I', directories), ('-iquote', quoted), ('-isystem', directories), ('-include', forced)):
            if argument == option:
                value = next(iterator, '')
            elif argument.startswith(option) and len(argument) > len(option):
                value = argument[len(option):]
            else:
                continue
            if value:
                target.append(value if target is forced else _resolve_entry_path(value, directory))
            break
    return directories, quoted, forced


def analyze(project, header, compile_commands=None, cache_dir=None, root_functions=None):
    project = Path(project).expanduser().resolve()
    header = Path(header).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f'Project directory does not exist: {project}')
    if not header.is_file() or not header.is_relative_to(project):
        raise ValueError('Header must exist inside the project directory')
    compile_database = load_compile_commands(compile_commands, project) if compile_commands is not None else None
    all_files, compile_database = _project_files(project, compile_database)
    bundles = {path: parse_file_cached(path, cache_dir, with_metadata=True) for path in all_files}
    definitions = [definition for bundle in bundles.values() for definition in bundle['definitions']]
    structures = [structure for bundle in bundles.values() for structure in bundle['structures']]
    header_roots = bundles.get(header, {}).get('declarations', [])
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

    # Includes establish visibility; conditional/macro expansion remains approximate.
    entries = {Path(entry['file']): entry for entry in (compile_database or {}).get('selected_sources', [])}
    visibility = {}
    for unit in all_files:
        entry = entries.get(unit, {})
        entry_directory = Path(entry.get('directory', project))
        directories, quoted, forced = include_options(entry.get('arguments', []), entry_directory)
        if not entry:
            directories.extend([project, project / 'include'])
        seen = set()

        def visit_file(path):
            if path in seen or path not in bundles:
                return
            seen.add(path)
            for include in bundles[path]['includes']:
                value = include[1:-1]
                search = ([path.parent, *quoted] if include.startswith('"') else []) + directories
                target = next((candidate for directory in search if (candidate := (directory / value).resolve()).is_file()), None)
                if target is not None:
                    visit_file(target)

        visit_file(unit)
        for value in forced:
            target = next((candidate for directory in [entry_directory, *quoted, *directories]
                           if (candidate := (directory / value).resolve()).is_file()), None)
            if target is not None:
                visit_file(target)
        visibility[str(unit.relative_to(project))] = seen

    for structure in structures:
        absolute = Path(structure['declaration']['file'])
        structure['id'] = f"{absolute.relative_to(project)}:{structure['declaration']['line']}:{structure['declaration']['column']}:{structure['kind']}"

    type_maps = {}
    for unit, visible in visibility.items():
        names = defaultdict(set)
        for structure in structures:
            if Path(structure['declaration']['file']) in visible:
                for value in [structure['name'], *structure.get('aliases', []), structure.get('anonymous_name')]:
                    if value is None:
                        continue
                    names[value].add(structure['id'])
        aliases = {name: targets for path in sorted(visible) for name, targets in bundles[path]['aliases'].items()}
        changed = True
        while changed:
            changed = False
            for alias, targets in aliases.items():
                resolved = set().union(*(names[target] for target in targets))
                if not resolved.issubset(names[alias]):
                    names[alias].update(resolved)
                    changed = True
        type_maps[unit] = names
    for structure in structures:
        contexts = [unit for unit, visible in visibility.items() if Path(structure['declaration']['file']) in visible]
        structure['type_references'] = sorted(set().union(*(type_maps[unit][name] for unit in contexts for field in structure['fields'] for name in field.get('type_names', []))))
        structure['aliases'] = sorted({name for unit in contexts for name, ids in type_maps[unit].items()
                                      if structure['id'] in ids and name != structure['name'] and not name.startswith('<anonymous@')})

    graph = nx.DiGraph()
    unresolved = []
    ambiguous = []
    indirect = []
    visited = set()

    def contextualize(definition, unit):
        result = dict(definition)
        result['translation_unit'] = unit
        if Path(definition['file']).suffix.lower() in HEADER_SUFFIXES and unit != definition['file']:
            result['id'] += f'@{unit}'
        names = type_maps.get(unit, {})
        result['type_references'] = sorted(set().union(*(names.get(name, set()) for name in definition.get('type_names', []))))
        visible_objects = set().union(*(set(bundles[path]['objects']) for path in visibility.get(unit, set())))
        object_kinds = {name: kind for path in sorted(visibility.get(unit, set())) for name, kind in bundles[path]['object_kinds'].items()}
        result['globals_read'], result['globals_written'] = global_effects(definition.get('identifier_uses', []), visible_objects, object_kinds)
        result['calls'] = [name for name in definition['calls'] if name not in visible_objects]
        result['indirect_call_details'] = list(definition['indirect_call_details'])
        for call in definition.get('direct_call_details', []):
            if call['name'] in visible_objects:
                result['indirect_call_details'].append({'expression': call['name'], 'binding': 'global', **{key: call[key] for key in ('file', 'line', 'column')}})
                result['globals_read'].append({'name': call['name'], 'source': location_from_detail(call)})
        return result

    def resolve(name, caller=None):
        candidates = by_name.get(name, [])
        if caller:
            unit = caller.get('translation_unit', caller['file'])
            visible = visibility.get(unit, set())
            candidates = [item for item in candidates if not item['static'] or project / item['file'] in visible]
            local = [item for item in candidates if item['static']]
            if local:
                candidates = local
        else:
            unit = None
            candidates = [item for item in candidates if not item['static']]
        candidates = [contextualize(item, unit if unit and Path(item['file']).suffix.lower() in HEADER_SUFFIXES else item['file']) for item in candidates]
        return candidates[0] if len(candidates) == 1 else candidates

    def indirect_candidates(expression, caller, binding=None):
        if binding in ('object', 'global'):
            return [], 'unresolved'
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
        details = []
        for item in definition['indirect_call_details']:
            candidates, confidence = indirect_candidates(item['expression'], definition, item.get('binding'))
            enriched = {**item, 'source': location_from_detail(item), 'confidence': confidence, 'candidates': candidates}
            details.append(enriched)
            indirect.append({'caller': definition['id'], 'expression': item['expression'], 'source': location_from_detail(item), 'confidence': confidence, 'candidates': candidates})
        graph.add_node(definition['id'], label=definition['name'], file=definition['file'], line=definition['line'], status='resolved', translation_unit=definition['translation_unit'], **{key: definition.get(key, []) for key in ('control_flow', 'accesses', 'globals_read', 'globals_written', 'type_references')}, indirect_call_details=details)
        for call in sorted(set(definition['calls'])):
            target = resolve(call, definition)
            if isinstance(target, dict):
                graph.add_node(target['id'], label=target['name'], file=target['file'], line=target['line'], status='resolved')
                from_include = target['file'] != target['translation_unit']
                graph.add_edge(definition['id'], target['id'], relation='calls', confidence='inferred' if from_include else 'confirmed', source='local_include' if from_include else 'direct_identifier')
                traverse(target)
            else:
                status = 'ambiguous' if target else 'unresolved'
                ident = f'{status}:{definition["id"]}:{call}'
                graph.add_node(ident, label=call, file='', line=0, status=status)
                graph.add_edge(definition['id'], ident, relation='calls', confidence=status, source='direct_identifier')
                (ambiguous if target else unresolved).append({'caller': definition['id'], 'name': call, 'candidates': [item['id'] for item in target]})

    root_info = []
    for name in header_roots:
        header_local = [item for item in by_name.get(name, []) if item['file'] == str(header.relative_to(project)) and item['static']]
        target = contextualize(header_local[0], str(header.relative_to(project))) if len(header_local) == 1 else (header_local if header_local else resolve(name))
        if isinstance(target, dict):
            root_info.append({'name': name, 'definition': target['id'], 'status': 'resolved', 'confidence': 'confirmed'})
            traverse(target)
        else:
            status = 'ambiguous' if target else 'unresolved'
            ident = f'{status}:root:{header.relative_to(project)}:{name}'
            graph.add_node(ident, label=name, file='', line=0, status=status)
            root_info.append({'name': name, 'definition': None, 'symbol': ident, 'status': status, 'confidence': status, 'candidates': [item['id'] for item in target]})
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
