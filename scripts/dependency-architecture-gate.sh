#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
[[ -d "$ROOT" ]] || { echo "Project directory not found: $ROOT" >&2; exit 2; }
ROOT="$(cd "$ROOT" && pwd)"
[[ -d "$ROOT/.ctrust" ]] || { echo "Dependency architecture gate skipped: .ctrust not found." >&2; exit 0; }

status=0
DOMAIN_FILES=()
for path in "$ROOT/crates/domain" "$ROOT/src/domain"; do
  [[ -d "$path" ]] || continue
  while IFS= read -r -d '' file; do DOMAIN_FILES+=("$file"); done < <(find "$path" -type f \( -name '*.rs' -o -name 'Cargo.toml' \) -print0)
done
if [[ ${#DOMAIN_FILES[@]} -eq 0 ]]; then
  echo "No domain crate path found; architecture dependency gate skipped." >&2
  exit 0
fi

for file in "${DOMAIN_FILES[@]}"; do
  if rg -n '(^|[^[:alnum:]_])(zenoh|zenoh-ext|serde_json)([^[:alnum:]_]|$)' "$file"; then
    echo "Forbidden transport dependency in domain: $file" >&2
    status=1
  fi
done

for file in "${DOMAIN_FILES[@]}"; do
  if [[ "$(basename "$file")" == Cargo.toml ]] && rg -n '^serde[[:space:]]*=' "$file" | grep -v 'optional[[:space:]]*=[[:space:]]*true' >/dev/null; then
    echo "Mandatory serde dependency in domain Cargo.toml: $file" >&2
    status=1
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "Dependency architecture gate failed." >&2
  exit 1
fi
echo "Dependency architecture gate passed."
