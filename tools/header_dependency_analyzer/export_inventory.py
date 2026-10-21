"""Consolidated header analysis and schema-3 inventory export, before slices."""
import argparse
import json
import re
import sys
from pathlib import Path

import networkx as nx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze import analyze, parse_file
from migration.transaction import atomic_write


def merge_analyses(project, headers, compile_commands, cache_dir=None, root_functions=None, include_dirs=None, dependency_roots=None):
    graph = nx.DiGraph()
    reports = []
    declared = set()
    for header in headers:
        selected_functions = root_functions
        if root_functions is not None:
            _, declarations = parse_file(header)
            declared.update(declarations)
            selected_functions = [name for name in root_functions if name in declarations]
            if not selected_functions:
                continue
        current_graph, report = analyze(project, header, compile_commands, cache_dir, selected_functions, include_dirs, dependency_roots)
        graph = nx.compose(graph, current_graph)
        reports.append(report)

    if root_functions is not None and not reports:
        raise ValueError("None of the requested functions is declared in the supplied headers")
    if root_functions is not None and (missing := set(root_functions) - declared):
        raise ValueError("Requested functions not declared in supplied headers: " + ", ".join(sorted(missing)))

    report = {
        "analyzer_version": reports[0].get("analyzer_version", "unknown") if reports else "unknown",
        "project": str(project.resolve()),
        "headers": [item["header"] for item in reports],
        "roots": [root for item in reports for root in item["roots"]],
        "unresolved_calls": [entry for item in reports for entry in item["unresolved_calls"]],
        "ambiguous_calls": [entry for item in reports for entry in item["ambiguous_calls"]],
        "indirect_calls": [entry for item in reports for entry in item["indirect_calls"]],
        "structures": [entry for item in reports for entry in item.get("structures", [])],
        "cycles": [entry for item in reports for entry in item.get("cycles", [])],
        "unresolved_includes": list({(entry['file'], entry['include']): entry for item in reports for entry in item.get('unresolved_includes', [])}.values()),
        "definitions_indexed": sum(item["definitions_indexed"] for item in reports),
        "files_indexed": max((item["files_indexed"] for item in reports), default=0),
    }
    if reports and "compile_commands" in reports[0]:
        report["compile_commands"] = reports[0]["compile_commands"]
        report["compile_commands_entries"] = reports[0]["compile_commands_entries"]
        report["translation_units_selected"] = reports[0]["translation_units_selected"]
    return graph, report


def compact_location(value):
    if not isinstance(value, dict):
        return value
    if "file" in value and "line" in value:
        return f"{value['file']}:{value['line']}"
    return value


def compact_behavior_function(graph, node, attrs):
    def compact_items(items, limit=24):
        values = list(items)
        result = values[:limit]
        if len(values) > limit:
            result.append({"truncated": len(values) - limit})
        return result

    calls = []
    for child in list(graph.successors(node))[:24]:
        child_attrs = graph.nodes[child]
        calls.append({
            "name": child_attrs.get("label", child),
            "function_id": child,
            "definition": compact_location({"file": child_attrs.get("file", ""), "line": child_attrs.get("line", 0)}) if child_attrs.get("file") else child_attrs.get("status", "unknown"),
        })
    return {
        "c_name": attrs.get("label", node),
        "function_id": node,
        "definition": compact_location({"file": attrs.get("file", ""), "line": attrs.get("line", 0)}),
        "control_flow": compact_items(attrs.get("control_flow", [])),
        "calls": calls,
        "accesses": compact_items([{"expression": item.get("expression", ""), "kind": item.get("kind", "field"), "source": compact_location(item.get("source", {}))} for item in attrs.get("accesses", [])]),
        "globals_read": compact_items([{"name": item.get("name", ""), "source": compact_location(item.get("source", {}))} for item in attrs.get("globals_read", [])]),
        "globals_written": compact_items([{"name": item.get("name", ""), "source": compact_location(item.get("source", {}))} for item in attrs.get("globals_written", [])]),
        "indirect_calls": compact_items([{"expression": item.get("expression", ""), "confidence": item.get("confidence", "unknown"), "candidates": item.get("candidates", []), "source": compact_location(item.get("source", {}))} for item in attrs.get("indirect_call_details", [])]),
    }


def compact_structures(structures, graph):
    referenced = set()
    for _, attrs in graph.nodes(data=True):
        referenced.update(attrs.get("type_references", []))

    structures = list({item.get('id', str(item.get('declaration'))): item for item in structures}.values())
    by_id = {item.get('id', item.get('name')): item for item in structures}
    queue = list(referenced)
    while queue:
        identity = queue.pop()
        structure = by_id.get(identity)
        if not structure:
            continue
        field_text = " ".join(field.get("declaration", "") for field in structure.get("fields", []))
        for candidate, other in by_id.items():
            same_file = other['declaration']['file'] == structure['declaration']['file']
            nested = same_file and structure.get('start_byte', -1) < other.get('start_byte', -1) < structure.get('end_byte', -1)
            names = [other.get('name', ''), *other.get('aliases', [])]
            named = any(name and name != '<anonymous>' and re.search(rf'\b{re.escape(name)}\b', field_text) for name in names)
            if candidate not in referenced and (nested or named):
                referenced.add(candidate)
                queue.append(candidate)

    compact = []
    for structure in structures:
        if structure.get('id', structure.get('name')) not in referenced:
            continue
        fields = []
        for field in structure.get("fields", []):
            fields.append({
                "name": field.get("name", ""),
                "declaration": field.get("declaration", ""),
                "pointer_depth": field.get("pointer_depth", 0),
                "source": compact_location(field.get("source", {})),
                "mapping": {"message_dto": "", "domain": "", "strategy": "unresolved", "exclusion_reason": ""},
            })
        declaration = structure.get("declaration", {})
        compact.append({
            "id": structure.get('id', ''),
            "name": structure.get("name", ""),
            "kind": structure.get("kind", ""),
            "declaration": compact_location(declaration),
            "mapping": {"message_dto": "", "domain": "", "strategy": "unresolved", "exclusion_reason": ""},
            "fields": fields,
        })
    return compact


def export_inventory(project, headers, output, compile_commands=None, cache_dir=None, root_functions=None):
    from migration.inventory import collect
    inventory = collect(project, headers, root_functions, compile_commands, cache_dir)
    output = Path(output).resolve()
    atomic_write(output.parent, output.name, yaml.safe_dump({'version': 3, **inventory}, sort_keys=False, allow_unicode=True).encode())
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--header', action='append', required=True)
    parser.add_argument('--function', action='append')
    parser.add_argument('--compile-commands', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_inventory(args.project, args.header, args.output, args.compile_commands, args.cache_dir, args.function)
        print(f'Exported {len(result["functions"])} unique functions, before slice assignment')
    except (ValueError, OSError) as exc:
        print(f'Inventory rejected: {exc}', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
