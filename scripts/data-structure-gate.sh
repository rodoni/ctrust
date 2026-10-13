#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
# Default to the active slice; a slice ID or --all selects another scope.
PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow gate "${1:-.}" structures "${2:-}"
