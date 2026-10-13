#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-.}"
SCOPE="${2:-}"
if [[ ! -d "$ROOT" ]]; then
  echo "Diretório não encontrado: $ROOT" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
if [[ ! -f "$ROOT/Cargo.toml" ]]; then
  echo "Cargo.toml não encontrado em: $ROOT" >&2
  exit 2
fi

cd "$ROOT"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow gate "$ROOT" prerequisites "$SCOPE"
"$SCRIPT_DIR/function-recovery-gate.sh" "$ROOT" "$SCOPE"
"$SCRIPT_DIR/dependency-architecture-gate.sh" "$ROOT"
"$SCRIPT_DIR/api-safety-gate.sh" "$ROOT"
"$SCRIPT_DIR/data-structure-gate.sh" "$ROOT" "$SCOPE"
"$SCRIPT_DIR/behavior-gate.sh" "$ROOT" "$SCOPE"
"$SCRIPT_DIR/organization-gate.sh" "$ROOT" "$SCOPE"
"$SCRIPT_DIR/migration-completeness.sh" "$ROOT" "$SCOPE"
"$SCRIPT_DIR/migration-comparison.sh" "$ROOT" "$SCOPE"
cargo fmt --all -- --check
cargo check --workspace
cargo test --workspace
