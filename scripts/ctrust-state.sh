#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="${SCRIPT_DIR%/scripts}/tools"
export PYTHONPATH="$TOOL_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m migration.cli "$@"
