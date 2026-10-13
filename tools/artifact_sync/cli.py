#!/usr/bin/env python3
import argparse
import json
from .engine import reconcile, validate

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["reconcile-artifacts", "reconcile", "validate-artifacts"])
    parser.add_argument("project")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--migrate-legacy", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate(args.project) if args.command == "validate-artifacts" else reconcile(args.project, args.dry_run, args.migrate_legacy)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status={result.status} selected_slice={result.selected_slice} committed={result.committed}")
        print(f"next_action={result.next_action}")
        for diagnostic in result.diagnostics + result.conflicts:
            print(f"{diagnostic.severity}:{diagnostic.code}:{diagnostic.path}:{diagnostic.message}")
    return 0 if result.status == "valid" else 1

if __name__ == "__main__":
    raise SystemExit(main())
