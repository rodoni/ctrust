#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
ROOT="${1:-.}"
if [[ "${2:-}" == --all ]]; then
    # Validate the saved plan for each declared slice, including separate crates.
    PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow organization-all "$ROOT"
else
    PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m organization.cli --project "$ROOT" --slice "${2:-}" --validate
fi
