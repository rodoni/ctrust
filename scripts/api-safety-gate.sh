#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
[[ -d "$ROOT" ]] || { echo "Project directory not found: $ROOT" >&2; exit 2; }
ROOT="$(cd "$ROOT" && pwd)"
[[ -d "$ROOT/.ctrust" ]] || { echo "API safety gate skipped: .ctrust not found." >&2; exit 0; }
if ! command -v rg >/dev/null 2>&1; then
  echo "api-safety-gate.sh requires rg." >&2
  exit 2
fi

status=0
SCAN=()
for path in "$ROOT/crates/domain" "$ROOT/src/domain"; do
  [[ -d "$path" ]] && SCAN+=("$path")
done
[[ ${#SCAN[@]} -gt 0 ]] || { echo "No domain path found; API safety gate skipped." >&2; exit 0; }

check() {
  local label="$1" pattern="$2"
  if rg -n --glob '*.rs' "$pattern" "${SCAN[@]}"; then
    echo "Domain API violation: $label" >&2
    status=1
  fi
}
check 'raw pointer' '\*mut[[:space:]]|\*const[[:space:]]'
check 'shared-memory offset/address' 'shared_memory|shared-memory|mmap|offset[[:space:]]*:'
check 'transport DTO or Zenoh import' 'use[[:space:]].*(zenoh|transport|Message_DTO|DtoMapper)|zenoh::|serde_json::'

if [[ "$status" -ne 0 ]]; then
  echo "API safety gate failed." >&2
  exit 1
fi
echo "API safety gate passed."
