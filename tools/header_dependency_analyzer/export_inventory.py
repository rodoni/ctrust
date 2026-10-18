"""Export header_dependency_analyzer results as ctrust YAML slice artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory_transaction import publish_inventory
from artifact_sync.transaction import TransactionError
from analyze import analyze, parse_file


def scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def yaml_lines(value, indent=0):
    prefix = " " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                if not item:
                    lines.append(f"{prefix}{key}: {'[]' if isinstance(item, list) else '{}'}")
                else:
                    lines.append(f"{prefix}{key}:")
                    lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                first = True
                for key, child in item.items():
                    if first:
                        if isinstance(child, (dict, list)):
                            if not child:
                                lines.append(f"{prefix}- {key}: {'[]' if isinstance(child, list) else '{}'}")
                            else:
                                lines.append(f"{prefix}- {key}:")
                                lines.extend(yaml_lines(child, indent + 4))
                        else:
                            lines.append(f"{prefix}- {key}: {scalar(child)}")
                        first = False
                    elif isinstance(child, (dict, list)):
                        if not child:
                            lines.append(f"{prefix}  {key}: {'[]' if isinstance(child, list) else '{}'}")
                        else:
                            lines.append(f"{prefix}  {key}:")
                            lines.extend(yaml_lines(child, indent + 4))
                    else:
                        lines.append(f"{prefix}  {key}: {scalar(child)}")
            elif isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {scalar(item)}")
    return lines


def write_yaml(path, value):
    path.write_text("\n".join(yaml_lines(value)) + "\n", encoding="utf-8")


def merge_analyses(project, headers, compile_commands, cache_dir=None, root_functions=None):
    graph = nx.DiGraph()
    reports = []
    for header in headers:
        selected_functions = root_functions
        if root_functions is not None:
            _, declarations = parse_file(header)
            selected_functions = [name for name in root_functions if name in declarations]
            if not selected_functions:
                continue
        current_graph, report = analyze(project, header, compile_commands, cache_dir, selected_functions)
        graph = nx.compose(graph, current_graph)
        reports.append(report)

    if root_functions is not None and not reports:
        raise ValueError("None of the requested functions is declared in the supplied headers")

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

    by_name = {item.get("name"): item for item in structures if item.get("name")}
    queue = list(referenced)
    while queue:
        name = queue.pop()
        structure = by_name.get(name)
        if not structure:
            continue
        field_text = " ".join(field.get("declaration", "") for field in structure.get("fields", []))
        for candidate in by_name:
            if candidate not in referenced and re.search(rf"\b{re.escape(candidate)}\b", field_text):
                referenced.add(candidate)
                queue.append(candidate)

    compact = []
    for structure in structures:
        if structure.get("name") not in referenced:
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
            "name": structure.get("name", ""),
            "kind": structure.get("kind", ""),
            "declaration": compact_location(declaration),
            "mapping": {"message_dto": "", "domain": "", "strategy": "unresolved", "exclusion_reason": ""},
            "fields": fields,
        })
    return compact


def load_symbol_ownership(output):
    """Load persisted ownership without making it a dependency of C analysis."""
    ownership = {}
    state_root = output.resolve().parents[2]
    for path in sorted((state_root / "plans/units").glob("*-symbol-map.yaml")):
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        for symbol in data.get("symbols", []):
            if isinstance(symbol, dict) and symbol.get("function_id"):
                ownership[symbol["function_id"]] = symbol
    return ownership


def behavior_for_graph(graph, report, slice_id, mode="compact", root_functions=None, ownership=None):
    ownership = ownership or {}
    root_nodes = {item.get("definition") for item in report.get("roots", []) if item.get("definition")}
    if root_functions is None:
        selected_nodes = {node for node, attrs in graph.nodes(data=True) if attrs.get("status") == "resolved"}
    else:
        selected_nodes = root_nodes
    functions = []
    references = []
    adapters = []
    seen_references = set()
    for node in sorted(selected_nodes):
        if node not in graph or graph.nodes[node].get("status") != "resolved":
            continue
        attrs = graph.nodes[node]
        if mode == "full":
            function = {
                "c_name": attrs.get("label", node),
                "function_id": node,
                "definition": f"{attrs.get('file', '')}:{attrs.get('line', '')}",
                "status": "generated",
                "control_flow": attrs.get("control_flow", []),
                "calls": [child for child in graph.successors(node)],
                "accesses": attrs.get("accesses", []),
                "globals_read": attrs.get("globals_read", []),
                "globals_written": attrs.get("globals_written", []),
                "indirect_calls": attrs.get("indirect_call_details", []),
            }
        else:
            function = compact_behavior_function(graph, node, attrs)
            function["function_id"] = node
        functions.append(function)
        for child in graph.successors(node):
            if child in selected_nodes or child in seen_references:
                continue
            child_attrs = graph.nodes[child]
            if child_attrs.get("status") != "resolved":
                continue
            seen_references.add(child)
            persisted = ownership.get(child, {})
            reference = {
                "function_id": child,
                "c_name": child_attrs.get("label", child),
                "owner_slice": persisted.get("owner_slice", ""),
                "rust_path": persisted.get("rust_path", ""),
                "role": "adapter" if persisted.get("reexport_required") is False and persisted.get("owner_slice") == slice_id else "reference",
                "extraction": "contract_only" if not persisted.get("owner_slice") else "not_repeated",
            }
            (adapters if reference["role"] == "adapter" else references).append(reference)
    return {
        "version": 2,
        "kind": "c-behavior",
        "slice": slice_id,
        "status": "draft",
        "source": {"headers": report["headers"], "analyzer": "header_dependency_analyzer"},
        "scope": {"roots": sorted(root_nodes), "selected_functions": sorted(selected_nodes), "reachable_functions": sorted(node for node in graph if graph.nodes[node].get("status") == "resolved")},
        "functions": functions,
        "references": references,
        "adapters": adapters,
        "unresolved_calls": report["unresolved_calls"],
        "ambiguous_calls": report["ambiguous_calls"],
        "cycles": report["cycles"],
        "completion": {
            "source_logic_read": False,
            "branches_mapped": bool(functions),
            "calls_mapped": not bool(report["unresolved_calls"] or report["ambiguous_calls"]),
            "errors_mapped": False,
            "side_effects_mapped": False,
            "test_cases_defined": False,
        },
    }


def export_slice(project, headers, output, slice_id, compile_commands=None, cache_dir=None, mode="compact", root_functions=None, replace_reviewed=False):
    graph, report = merge_analyses(project, headers, compile_commands, cache_dir, root_functions)

    nodes = []
    files = set()
    for node, attrs in sorted(graph.nodes(data=True)):
        item = {"id": node, "name": attrs.get("label", node), "status": attrs.get("status", "resolved")}
        if attrs.get("file"):
            item["definition"] = compact_location({"file": attrs["file"], "line": attrs.get("line", 0)})
            files.add(attrs["file"])
        nodes.append(item)

    function_map = []
    for node, attrs in sorted(graph.nodes(data=True)):
        function_map.append({
            "c_name": attrs.get("label", node),
            "function_id": node,
            "rust_name": "",
            "rust_file": "",
            "module_path": "",
            "unit_id": "",
            "state": "planned" if attrs.get("status") == "resolved" else "blocked",
            "implementation_status": "pending",
            "test_status": "pending",
            "comparison_status": "pending",
            "evidence_refs": [],
            "definition_file": attrs.get("file", ""),
            "definition_line": attrs.get("line", None),
            "status": attrs.get("status", "resolved"),
            "confidence": "confirmed" if attrs.get("status") == "resolved" else attrs.get("status", "unknown"),
            "source": "header_dependency_analyzer",
        })

    edges = []
    callers = []
    for caller, callee, attrs in sorted(graph.edges(data=True)):
        edges.append({"caller": caller, "callee": callee, "relation": attrs.get("relation", "calls"), "confidence": attrs.get("confidence", "unknown"), "source": "header_dependency_analyzer"})
        callers.append({"caller": caller, "callee": callee, "evidence": "analyzer graph", "status": graph.nodes[callee].get("status", "resolved"), "confidence": attrs.get("confidence", "unknown")})

    dependencies = {
        "version": 1,
        "kind": "header-dependency-analysis",
        "status": "generated",
        "slice": slice_id,
        "analyzer": "header_dependency_analyzer",
        "headers": report["headers"],
        "roots": report["roots"],
        "edges": edges,
        "unresolved_calls": report["unresolved_calls"],
        "ambiguous_calls": report["ambiguous_calls"],
        "indirect_calls": report["indirect_calls"],
        "cycles": report["cycles"],
        "compile_commands": report.get("compile_commands", None),
        "translation_units_selected": report.get("translation_units_selected", None),
    }

    structures = report["structures"] if mode == "full" else compact_structures(report["structures"], graph)
    ownership = load_symbol_ownership(output)
    behavior = behavior_for_graph(graph, report, slice_id, mode, root_functions, ownership)
    summary = {
        "functions": len([node for node, attrs in graph.nodes(data=True) if attrs.get("status") == "resolved"]),
        "structures": len(structures),
        "dependencies": len(edges),
        "unresolved_calls": len(report["unresolved_calls"]),
        "ambiguous_calls": len(report["ambiguous_calls"]),
        "indirect_calls": len(report["indirect_calls"]),
        "cycles": len(report["cycles"]),
    }
    data_structures = {
        "version": 1,
        "kind": "c-data-structures",
        "slice": slice_id,
        "status": "draft",
        "structures": structures,
        "summary": {"structures": len(structures)},
        "completion": {"structures_identified": bool(structures), "fields_mapped": bool(structures), "layout_mapped": False, "pointer_semantics_mapped": False, "shared_memory_classified": False, "rust_strategy_defined": False, "evidence_recorded": bool(structures)},
    }
    manifest_source = report if mode == "full" else {"headers": report["headers"], "analyzer": "header_dependency_analyzer", "analyzer_version": report.get("analyzer_version", "unknown")}

    def generate(stage, generation):
        write_yaml(stage / "data-structures.yaml", data_structures)
        behavior_index = dict(behavior)
        detailed = behavior.get("functions", [])
        summaries = []
        for function in detailed:
            function_id = function.get("function_id", function.get("c_name", "function"))
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", function.get("c_name", "function")).strip("_") or "function"
            artifact = f"behavior/functions/{safe_name}.yaml"
            artifact_path = stage / artifact
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            write_yaml(artifact_path, function)
            summaries.append({"c_name": function.get("c_name", ""), "function_id": function_id, "definition": function.get("definition", ""), "artifact": artifact, "test_cases": function.get("test_cases", [])})
        behavior_index["functions"] = summaries
        behavior_index["artifacts"] = {"functions": "behavior/functions/"}
        write_yaml(stage / "behavior.yaml", behavior_index)
        mapping_items = []
        for structure in structures:
            mapping_items.append({"c_name": structure.get("name", ""), "message_dto": "", "domain": "", "strategy": "unresolved"})
        write_yaml(stage / "mapping-confirmation.yaml", {"version": 1, "kind": "data-structure-mapping-confirmation", "slice": slice_id, "status": "draft", "revision": 1, "counts": {"reachable": len(structures), "classified": 0, "excluded": 0, "unresolved": len(mapping_items)}, "mappings": mapping_items, "exclusions": []})
        write_yaml(stage / "manifest.yaml", {"version": 1, "kind": "header-dependency-inventory", "slice": slice_id, "status": "generated", "mode": mode, "summary": summary, "source": manifest_source, "artifacts": {"data_structures": "data-structures.yaml", "mapping_confirmation": "mapping-confirmation.yaml", "behavior": "behavior.yaml"}})
        write_yaml(stage / "symbols.yaml", {"version": 1, "symbols": nodes})
        write_yaml(stage / "function-map.yaml", {"version": 2, "slice": slice_id, "revision": 1, "units": [], "source": "header_dependency_analyzer", "functions": function_map})
        write_yaml(stage / "dependencies.yaml", dependencies)
        write_yaml(stage / "callers.yaml", {"version": 1, "source": "header_dependency_analyzer", "edges": [{"caller": item["caller"], "callee": item["callee"], "confidence": item["confidence"]} for item in callers]})
        write_yaml(stage / "files.yaml", {"version": 1, "files": sorted(files), "headers": report["headers"]})
        manifest_path = stage / "manifest.yaml"
        from workflow import load, INVENTORY_FIELDS
        manifest = load(manifest_path)
        manifest["generation"] = generation
        manifest["artifact_digests"] = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in INVENTORY_FIELDS if name != "manifest.yaml"}
        write_yaml(manifest_path, manifest)
    publish_inventory(output, slice_id, generate, replace_reviewed)
    return report, graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--header", required=True, action="append", type=Path)
    parser.add_argument("--slice", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--function", action="append", default=[], help="Function declared in the input header and belonging to this slice; repeat for multiple roots")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--mode", choices=("compact", "full"), default="compact")
    parser.add_argument("--replace-reviewed", action="store_true", help="Explicitly archive and replace an enriched or confirmed inventory")
    args = parser.parse_args()
    try:
        report, graph = export_slice(args.project, args.header, args.output, args.slice, args.compile_commands, args.cache_dir, args.mode, args.function, args.replace_reviewed)
    except (TransactionError, ValueError, OSError) as exc:
        print(f"Inventory blocked: {exc}", file=sys.stderr)
        return 3
    print(f"Exported {len(graph.nodes)} symbols and {len(graph.edges)} dependencies for {args.slice}")
    print(f"Unresolved: {len(report['unresolved_calls'])}; ambiguous: {len(report['ambiguous_calls'])}; indirect: {len(report['indirect_calls'])}")


if __name__ == "__main__":
    raise SystemExit(main())
