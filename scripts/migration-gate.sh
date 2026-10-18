#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-.}"
SCOPE="${2:-all}"
[[ "$SCOPE" != "--all" ]] || SCOPE=all
exec "$SCRIPT_DIR/ctrust-state.sh" gate "$ROOT" "$SCOPE"
