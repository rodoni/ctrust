#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
if [[ ! -d "$ROOT" ]]; then
  echo "Rust project directory not found: $ROOT" >&2
  exit 2
fi

if [[ ! -d "$ROOT/.ctrust" ]]; then
  echo "Implementation completeness gate skipped: .ctrust not found." >&2
  exit 0
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "migration-completeness.sh requires ripgrep (rg)." >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
files="$(PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow scan-files "$ROOT" "${2:-}")"
mapfile -t SCAN_PATHS <<< "$files"

status=0
printf 'Implementation completeness scan: %s\n' "$ROOT"

check_pattern() {
  local label="$1"
  local pattern="$2"
  if rg -n --glob '*.rs' --glob '!target/**' "$pattern" "${SCAN_PATHS[@]}"; then
    echo "Stub/placeholder detected: $label" >&2
    status=1
  fi
}

check_pattern 'unfinished macro' '(^|[^[:alnum:]_])(todo!|unimplemented!)([^[:alnum:]_]|$)'
check_pattern 'placeholder panic' 'panic!\s*\([^\n]*(TODO|not implemented|stub|placeholder|未実装)'
check_pattern 'template token' 'TODO_FUNCTION|FUNCTION_NAME|SLICE_ID|UNIT_NAME'
check_pattern 'default result placeholder' '(^|[^[:alnum:]_])(Ok|Err)\s*\(\s*Default::default\(\)\s*\)'
# Constant returns may be valid C behavior; warn below and require test evidence.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
if ! PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow gate "$ROOT" sources "${2:-}"; then
  status=1
fi

if rg -n --glob '*.rs' --glob '!target/**' 'return\s+(0|false|None)\s*;' "${SCAN_PATHS[@]}"; then
  echo "Warning: constant return detected. Confirm it is derived from the C contract; do not use it as a default implementation." >&2
fi

if [[ "$status" -ne 0 ]]; then
  echo "Implementation completeness gate failed. Implement the behavior from the C source and record evidence before verification." >&2
  exit 1
fi

echo "Implementation completeness gate passed."
