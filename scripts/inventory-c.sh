#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
if [[ ! -d "$ROOT" ]]; then
  echo "Directory not found: $ROOT" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
mapfile -d '' SOURCES < <(
  find "$ROOT" -type f \( -name '*.c' -o -name '*.h' -o -name '*.cc' -o -name '*.cpp' -o -name '*.hpp' \) -print0 | sort -z
)

printf 'version: 1\n'
printf 'kind: c-source-inventory\n'
printf 'status: generated\n'
printf 'root: "%s"\n' "$ROOT"
printf 'source_files:\n'
for source in "${SOURCES[@]}"; do
  printf '  - path: "%s"\n' "$source"
  printf '    language: "%s"\n' "${source##*.}"
done

printf 'preprocessor_directives:\n'
for source in "${SOURCES[@]}"; do
  while IFS=: read -r line content; do
    [ -n "${line:-}" ] || continue
    escaped="${content//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    printf '  - file: "%s"\n    line: %s\n    text: "%s"\n' "$source" "$line" "$escaped"
  done < <(grep -nE '^\s*#\s*(define|if|ifdef|ifndef|elif|include)' "$source" 2>/dev/null || true)
done
