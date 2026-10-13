#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
OUTPUT="${2:-}"

if [[ ! -d "$ROOT" ]]; then
  echo "C source directory not found: $ROOT" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
if [[ -n "$OUTPUT" ]]; then
  mkdir -p "$(dirname "$OUTPUT")"
  OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"
fi

mapfile -d '' SOURCES < <(
  find "$ROOT" -type f \( -name '*.c' -o -name '*.h' -o -name '*.l' -o -name '*.y' \) -print0 | sort -z
)

if [[ "${#SOURCES[@]}" -eq 0 ]]; then
  echo "No C source files found under: $ROOT" >&2
  exit 2
fi

CSCOPE="$(command -v cscope || true)"
if [[ -z "$CSCOPE" ]]; then
  if [[ -n "$OUTPUT" ]]; then
    cat > "$OUTPUT" <<EOF
version: 1
kind: cscope-call-graph
status: unavailable
source_root: "$ROOT"
tool:
  name: cscope
  required: true
limitations:
  - "cscope is not installed"
  - "Do not infer slice order from filenames or a partial inventory"
files: []
EOF
  else
    printf 'version: 1\nkind: cscope-call-graph\nstatus: unavailable\nsource_root: "%s"\ntool:\n  name: cscope\n  required: true\n' "$ROOT"
  fi
  echo "cscope not found; call and symbol analysis unavailable" >&2
  exit 4
fi

WORK_DIR=""
DB_DIR=""
if [[ -n "$OUTPUT" ]]; then
  DB_DIR="$(dirname "$OUTPUT")"
  FILE_LIST="$DB_DIR/cscope.files"
  DB="$DB_DIR/cscope.out"
  printf '%s\n' "${SOURCES[@]}" > "$FILE_LIST"
else
  WORK_DIR="$(mktemp -d)"
  FILE_LIST="$WORK_DIR/cscope.files"
  DB="$WORK_DIR/cscope.out"
  printf '%s\n' "${SOURCES[@]}" > "$FILE_LIST"
fi
trap 'if [[ -n "$WORK_DIR" ]]; then rm -rf "$WORK_DIR"; fi' EXIT

BUILD_ERR_DIR="${WORK_DIR:-$DB_DIR}"
if ! (cd "$ROOT" && "$CSCOPE" -b -q -f "$DB" -i "$FILE_LIST") >/dev/null 2>"$BUILD_ERR_DIR/cscope-build.err"; then
  echo "cscope could not build the cross-reference:" >&2
  cat "$BUILD_ERR_DIR/cscope-build.err" >&2
  exit 5
fi

VERSION="$($CSCOPE -V 2>&1 | head -n 1 || true)"
[ -n "$VERSION" ] || VERSION="version unavailable"

write_report() {
  local target="$1"
  {
    printf 'version: 1\n'
    printf 'kind: cscope-call-graph\n'
    printf 'status: generated\n'
    printf 'source_root: "%s"\n' "$ROOT"
    printf 'tool:\n'
    printf '  name: cscope\n'
    printf '  path: "%s"\n' "$CSCOPE"
    printf '  version: "%s"\n' "$VERSION"
    printf 'database: "%s"\n' "$DB"
    printf 'file_list: "%s"\n' "$FILE_LIST"
    printf 'build_command: "cscope -b -q -f %s -i %s"\n' "$DB" "$FILE_LIST"
    printf 'queries:\n'
    printf '  - id: callees\n    command: "cscope -d -f %s -L -2 <function>"\n    meaning: functions_called_by_function\n' "$DB"
    printf '  - id: callers\n    command: "cscope -d -f %s -L -3 <function>"\n    meaning: functions_calling_function\n' "$DB"
    printf 'limitations:\n'
    printf '  - "Function pointers, callbacks and macros require manual review"\n'
    printf '  - "Unresolved symbols and generated sources require manual review"\n'
    printf '  - "This report is evidence for ordering and does not modify the slice manifest"\n'
    printf 'source_files:\n'
    for source in "${SOURCES[@]}"; do
      printf '  - path: "%s"\n' "$source"
    done
  } > "$target"
}

if [[ -n "$OUTPUT" ]]; then
  write_report "$OUTPUT"
  echo "cscope YAML report recorded in $OUTPUT" >&2
else
  write_report /dev/stdout
fi
