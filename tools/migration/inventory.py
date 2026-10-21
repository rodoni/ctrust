"""Consolidate a scoped call graph before slices exist."""
from pathlib import Path
import sys

from .store import MigrationError, digest, file_hash


def scoped_structures(inventory, functions):
    analyzer_dir = Path(__file__).resolve().parents[1] / 'header_dependency_analyzer'
    if str(analyzer_dir) not in sys.path:
        sys.path.insert(0, str(analyzer_dir))
    import networkx as nx
    from export_inventory import compact_structures
    graph = nx.DiGraph()
    for fid in functions:
        graph.add_node(fid, **inventory['functions'][fid])
    return compact_structures(inventory['structures'], graph)


def scoped_behavior(inventory, functions):
    analyzer_dir = Path(__file__).resolve().parents[1] / 'header_dependency_analyzer'
    if str(analyzer_dir) not in sys.path:
        sys.path.insert(0, str(analyzer_dir))
    import networkx as nx
    from export_inventory import compact_behavior_function
    graph = nx.DiGraph()
    for fid in functions:
        graph.add_node(fid, **inventory['functions'][fid])
        for child in inventory['functions'][fid].get('calls', []):
            graph.add_node(child, **inventory['functions'][child])
            graph.add_edge(fid, child)
    return {fid: compact_behavior_function(graph, fid, graph.nodes[fid]) for fid in functions}


def collect(source, headers, functions=None, compile_commands=None, cache=None):
    analyzer_dir = Path(__file__).resolve().parents[1] / 'header_dependency_analyzer'
    if str(analyzer_dir) not in sys.path:
        sys.path.insert(0, str(analyzer_dir))
    from export_inventory import merge_analyses
    from analyze import cached_bundle
    source = Path(source).resolve()
    if not headers:
        headers = sorted(str(path.relative_to(source)) for path in source.rglob('*')
                         if path.is_file() and path.suffix.lower() == '.h')
        if not headers:
            raise MigrationError(f'No C header files found recursively under source root: {source}; provide --header explicitly')
    def snapshot():
        return {str(p.relative_to(source)): file_hash(p) for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in {'.c', '.h'}}
    before_files = snapshot()
    before_database = file_hash(compile_commands) if compile_commands else None
    headers = [(source / value).resolve() for value in headers]
    prototypes = {}
    for header in headers:
        for item in cached_bundle(header, cache).get('function_declarations', []):
            prototypes.setdefault(item['name'], item)
    graph, report = merge_analyses(source, headers, compile_commands, cache, functions)
    identities = {}
    for node, attrs in graph.nodes(data=True):
        if attrs.get('status') == 'resolved':
            identities[node] = 'c:' + attrs['file'] + ':' + attrs['label'] + ('@' + attrs['translation_unit'] if attrs.get('translation_unit') else '')
    for node, attrs in graph.nodes(data=True):
        if node not in identities:
            identities[node] = 'dependency:' + digest({'caller': sorted(identities[p] for p in graph.predecessors(node)), 'name': attrs.get('label'), 'status': attrs.get('status')})[:24]
    catalog = {}
    for node, attrs in graph.nodes(data=True):
        identity = identities[node]
        item = {'id': identity, **attrs}
        if identity in catalog and catalog[identity].get('source_hash') != item.get('source_hash'):
            raise MigrationError(f'Conflicting definitions for {identity}')
        item['calls'] = sorted(identities[c] for c in graph.successors(node))
        item['indirect_calls'] = [detail for detail in report['indirect_calls'] if detail['caller'] == node]
        catalog[identity] = item
    roots = []
    for root in report['roots']:
        if root.get('definition'):
            roots.append(identities[root['definition']])
        else:
            identity = 'root:' + root['name']
            prototype = prototypes.get(root['name'], {})
            catalog[identity] = {'id': identity, 'label': root['name'], 'status': root['status'], 'candidates': root.get('candidates', []), 'calls': [], **({'signature': prototype['signature'], 'declaration': {'file': str(Path(prototype['file']).relative_to(source)), 'line': prototype['line']}} if prototype else {})}
            roots.append(identity)
    if not catalog:
        raise MigrationError(f'No functions were indexed from headers under source root: {source}; check header declarations and source file extensions')
    # Include all scanned sources and headers: changes in type declarations or
    # candidate resolution can affect the scoped graph even outside its nodes.
    files = snapshot()
    if files != before_files or (compile_commands and file_hash(compile_commands) != before_database):
        raise MigrationError('C sources or compilation database changed during inventory; repeat analysis')
    if compile_commands:
        files_db = {'path': str(Path(compile_commands).resolve()), 'hash': file_hash(compile_commands)}
    else:
        files_db = None
    payload = {'source_root': str(source), 'functions': dict(sorted(catalog.items())), 'roots': sorted(set(roots)), 'files': files, 'compile_commands': files_db, 'structures': list({s['id']: s for s in report['structures']}.values()), 'unresolved_includes': report['unresolved_includes']}
    payload['fingerprint'] = digest(payload)
    return payload
