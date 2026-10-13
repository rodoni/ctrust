#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
if [[ ! -d "$ROOT" ]]; then
  echo "Diretório não encontrado: $ROOT" >&2
  exit 2
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "check-ffi-surface.sh requer ripgrep (rg)." >&2
  exit 2
fi

status=0
printf 'FFI surface scan: %s\n' "$ROOT"
for pattern in 'extern[[:space:]]+"C"' '#\[[[:space:]]*repr\(C\)' '\bunsafe\b' 'from_raw_parts' 'CString' 'CStr'; do
  if rg -n --glob '*.rs' "$pattern" "$ROOT"; then
    status=1
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "Review required: FFI/unsafe markers were found." >&2
else
  echo "No known FFI/unsafe marker found. This is not proof of safety."
fi
exit "$status"
