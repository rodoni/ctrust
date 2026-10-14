#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
# Validate the active slice, a specific slice, or all slices with --all.
PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow gate "${1:-.}" inventory "${2:-}"
