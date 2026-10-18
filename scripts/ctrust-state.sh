#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND="${1:-help}"
ROOT="${2:-.}"
TITLE="${3:-checkpoint}"
MODE="${4:-}"
ARG5="${5:-}"
AUTO_RECONCILE=0
STATE="$ROOT/.ctrust"
TEMPLATE_ROOT="${SCRIPT_DIR%/scripts}"

usage() {
    cat <<'EOF'
Usage: ./ctrust-state.sh <command> [project] [args...]

Commands:
  init       [project] Initialize a persistent ctrust migration workspace
  upgrade-state [project] Upgrade legacy migration.yaml to schema version 2
  doctor     [project] Run non-destructive state/artifact consistency diagnostics
  behavior-function [project] <slice> <function> Review one behavior artifact
  status     [project] Show the compact migration state
  status-slices [project] Generate the per-slice migration cycle table
  reconcile-artifacts [project] Reconcile .ctrust artifacts deterministically
  validate-artifacts [project] Validate .ctrust artifacts without writing
  plan-slices [project] Create one plan Markdown file for every slice
  inventory  [project] [slice] Export inventory transactionally from the slice manifest
  recover    [project] Execute one eligible mechanical recovery; preserve blockers
  organize   [project] Generate deterministic organization plan
  validate-organization [project] Validate organization without writing
  resume     [project] [--brief|--full] Print recovery context (default --brief)
  function-map [project] [slice] Validate/enrich function-level map
  select-function [project] [slice] Select the next function unit
  record-implementation [project] <unit> [rust_file|auto] <module_path> Record implementation
  record-evidence [project] <unit> [evidence_path|auto] Record test evidence; auto uses the active function convention
  verify-slice [project] [slice] Run gates, checkpoint and advance
  compare [project] <unit> <passed|failed> Record C/Rust comparison
  resume     [project] [--brief|--full] Print recovery context (default --brief)
  model      [project] [unit] Create the Rust modeling template (confirmed contract required)
  checkpoint [project] [title] Create a numbered recovery checkpoint
  help                    Show this help message

Initialization and templates preserve existing files; progress commands update managed state.
EOF
}

write_if_missing() {
    local path="$1"
    if [ ! -e "$path" ]; then
        mkdir -p "$(dirname "$path")"
        cat > "$path"
        echo "created $path"
    else
        cat >/dev/null
        echo "preserved $path"
    fi
}

upgrade_state() {
    require_workspace
    workflow_run upgrade-state
    reconcile_state
}

init_workspace() {
    mkdir -p \
        "$STATE/inventory" \
        "$STATE/contracts/units" \
        "$STATE/plans/units" \
        "$STATE/decisions" \
        "$STATE/checkpoints" \
        "$STATE/evidence/tests" \
        "$STATE/evidence/builds" \
        "$STATE/evidence/comparisons" \
        "$STATE/logs"

    write_if_missing "$STATE/migration.yaml" <<EOF
version: 2
project: $(basename "$(cd "$ROOT" && pwd)")
source:
  project_root: ""
  component: ""
  files: []
target:
  workspace_root: $(cd "$ROOT" && pwd)
  project_root: $(cd "$ROOT" && pwd)
  path: ""
output:
  kind: library
  library_kind: rlib
  binary_name: ""
status: initialized
phase: inventory
current_unit: none
active_unit: none
active_functions: []
next_function: none
implementation_status: pending
test_status: pending
comparison_status: pending
function_map_revision: 0
last_checkpoint: none
next_action: "Run migration-scoping and define the slice manifest before inventory"
next_command: ""
next_owner: ctrust-lead
next_skill: migration-scoping
attempts: 0
max_attempts: 2
last_error: ""
blocked_by: []
risk_level: unknown
updated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

    write_if_missing "$STATE/current-context.md" <<'EOF'
# Current Context

- Phase: inventory
- Unit: none
- Status: initialized
- Last checkpoint: none
- Next action: run migration-scoping and define the slice manifest before inventory
- Open questions: none

## Facts

- Migration workspace initialized by ctrust.

## Decisions

- None recorded.

## Do not repeat

- No analysis has been completed yet.
EOF

    write_if_missing "$STATE/inventory/README.md" <<'EOF'
# Inventory

Store compact, reproducible inventory artifacts here. Prefer generated files and references to source locations over copied source code.
EOF

    write_if_missing "$STATE/contracts/index.yaml" <<'EOF'
version: 1
contracts: []
EOF

    write_if_missing "$STATE/plans/migration-map.yaml" <<'EOF'
version: 1
current_unit: none
units: []
EOF

    write_if_missing "$STATE/decisions/index.yaml" <<'EOF'
version: 1
decisions: []
EOF

    write_if_missing "$STATE/logs/progress.ndjson" <<EOF
{"ts":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","event":"workspace_initialized","phase":"inventory"}
EOF
    sync_context
    if compgen -G "$STATE/plans/units/*-slices.yaml" > /dev/null; then
        reconcile_state
    fi
}

workspace_not_initialized() {
    echo "ctrust migration workspace not initialized: $STATE" >&2
    echo "Bootstrap this destination before using resume/status:" >&2
    echo "  $0 init $ROOT" >&2
    echo "After init, configure source.project_root and run migration-scoping." >&2
}

require_workspace() {
    if [ ! -f "$STATE/migration.yaml" ]; then
        workspace_not_initialized
        return 2
    fi
}

create_unit_file() {
    local source="$1"
    local destination="$2"
    local unit="$3"
    local content

    if [ -e "$destination" ]; then
        echo "preserved $destination"
        return 0
    fi
    if [ ! -f "$source" ]; then
        echo "template not found: $source" >&2
        return 2
    fi
    mkdir -p "$(dirname "$destination")"
    sed "s/UNIT_NAME/$unit/g; s/SLICE_ID/$unit/g; s/FUNCTION_NAME/TODO_FUNCTION/g" "$source" > "$destination"
    echo "created $destination"
}

contract() {
    require_workspace
    workflow_run contract "$TITLE"
    sync_context
}

model() {
    require_workspace
    workflow_run model "$TITLE"
    sync_context
}

inventory() {
    require_workspace
    workflow_run inventory "$TITLE"
    sync_context
}

recover() {
    require_workspace
    workflow_run recover
    sync_context
}

state_value() {
    workflow_run read-state "$1"
}

state_child() {
    workflow_run read-state "$1" "$2"
}

sync_context() {
    workflow_run sync-context
}

plan_slices() {
    require_workspace
    workflow_run plan-slices
}

workflow_run() {
    local tool_root="${TEMPLATE_ROOT}/tools"
    PYTHONPATH="$tool_root${PYTHONPATH:+:$PYTHONPATH}" python3 -m workflow "$1" "$ROOT" "${@:2}"
}

reconcile_state() {
    require_workspace
    workflow_run reconcile
    plan_slices
    sync_context
    # Avoid status-slices bootstrapping reconciliation a second time.
    AUTO_RECONCILE=1
    status_slices >/dev/null
    AUTO_RECONCILE=0
}

reconcile_artifacts() {
    require_workspace
    local tool_root="${TEMPLATE_ROOT}/tools"
    local options=()
    [ "$TITLE" = "--dry-run" ] && options+=("--dry-run")
    [ "$TITLE" = "--json" ] && options+=("--json")
    [ "$MODE" = "--dry-run" ] && options+=("--dry-run")
    [ "$MODE" = "--migrate-legacy" ] && options+=("--migrate-legacy")
    PYTHONPATH="$tool_root${PYTHONPATH:+:$PYTHONPATH}" python3 -m artifact_sync.cli reconcile-artifacts "$ROOT" "${options[@]}"
}

validate_artifacts() {
    require_workspace
    local tool_root="${TEMPLATE_ROOT}/tools"
    PYTHONPATH="$tool_root${PYTHONPATH:+:$PYTHONPATH}" python3 -m artifact_sync.cli validate-artifacts "$ROOT"
}

organize() {
    require_workspace
    workflow_run organize "$TITLE"
    sync_context
}

validate_organization() {
    require_workspace
    "$SCRIPT_DIR/organization-gate.sh" "$ROOT" "${TITLE/checkpoint/}"
}

function_map() {
    require_workspace
    workflow_run function-map "$TITLE"
}

select_function() {
    require_workspace
    workflow_run select-function "$TITLE"
    sync_context
    status_slices >/dev/null
}

record_implementation() {
    require_workspace
    workflow_run record-implementation "$TITLE" "$MODE" "$ARG5"
    sync_context
    status_slices >/dev/null
}

record_evidence() {
    require_workspace
    workflow_run record-evidence "$TITLE" "$MODE"
    sync_context
    status_slices >/dev/null
}

compare_function() {
    require_workspace
    if [ -n "$ARG5" ]; then
        echo "Invalid compare arguments: use compare <unit-id> <passed|failed>; the active function is already stored in migration.yaml." >&2
        return 2
    fi
    workflow_run compare "$TITLE" "$MODE"
    sync_context
    status_slices >/dev/null
}

status() {
    require_workspace
    printf 'Project: %s\n' "$(state_value project)"
    printf 'Source C project: %s\n' "$(state_child source project_root)"
    printf 'Source component: %s\n' "$(state_child source component)"
    printf 'Target Rust workspace: %s\n' "$(state_child target workspace_root)"
    printf 'Target Rust project: %s\n' "$(state_child target project_root)"
    printf 'Target path: %s\n' "$(state_child target path)"
    printf 'Output kind: %s\n' "$(state_child output kind)"
    printf 'Status: %s\n' "$(state_value status)"
    printf 'Phase: %s\n' "$(state_value phase)"
    printf 'Unit: %s\n' "$(state_value current_unit)"
    printf 'Last checkpoint: %s\n' "$(state_value last_checkpoint)"
    printf 'Next action: %s\n' "$(state_value next_action)"
    printf 'Risk: %s\n' "$(state_value risk_level)"
}

status_slices() {
    require_workspace
    if [ "$(state_value current_unit)" = "none" ] && [ "$AUTO_RECONCILE" -eq 0 ] && compgen -G "$STATE/plans/units/*-slices.yaml" > /dev/null; then
        AUTO_RECONCILE=1
        reconcile_state
        AUTO_RECONCILE=0
        return $?
    fi
    sync_context
    workflow_run status-slices
}

resume_brief() {
    local attempts
    local max_attempts
    attempts="$(state_value attempts)"
    max_attempts="$(state_value max_attempts)"
    [ -n "$attempts" ] || attempts=0
    [ -n "$max_attempts" ] || max_attempts=2

    printf 'PHASE=%s\n' "$(state_value phase)"
    printf 'ACTIVE_SLICE=%s\n' "$(state_value current_unit)"
    printf 'ACTIVE_UNIT=%s\n' "$(state_value active_unit)"
    printf 'ACTIVE_FUNCTIONS=%s\n' "$(state_value active_functions)"
    printf 'NEXT_FUNCTION=%s\n' "$(state_value next_function)"
    printf 'IMPLEMENTATION_STATUS=%s\n' "$(state_value implementation_status)"
    printf 'TEST_STATUS=%s\n' "$(state_value test_status)"
    printf 'COMPARISON_STATUS=%s\n' "$(state_value comparison_status)"
    printf 'ACTIVE_RUST_FILE=%s\n' "$(state_value active_rust_file)"
    printf 'ACTIVE_MODULE_PATH=%s\n' "$(state_value active_module_path)"

    printf 'UNIT=%s\n' "$(state_value current_unit)"
    printf 'STATUS=%s\n' "$(state_value status)"
    printf 'NEXT_ACTION=%s\n' "$(state_value next_action)"
    printf 'NEXT_COMMAND=%s\n' "$(state_value next_command)"
    printf 'NEXT_OWNER=%s\n' "$(state_value next_owner)"
    printf 'NEXT_SKILL=%s\n' "$(state_value next_skill)"
    printf 'RECOVERY_CODE=%s\n' "$(state_child recovery code)"
    printf 'RECOVERY_ACTION=%s\n' "$(state_child recovery action)"
    printf 'ELIGIBLE_ACTIONS=%s\n' "$(state_value eligible_actions)"
    printf 'LAST_CHECKPOINT=%s\n' "$(state_value last_checkpoint)"
    printf 'ATTEMPTS=%s/%s\n' "$attempts" "$max_attempts"
    printf 'LAST_ERROR=%s\n' "$(state_value last_error)"

    if [ "$(state_value status)" = "blocked" ] || [ "$attempts" -ge "$max_attempts" ] 2>/dev/null; then
        printf 'LOOP_GUARD=stop\n'
        printf 'GUIDANCE=Do not retry the same action. Report the blocker to the user.\n'
    elif [ -z "$(state_value next_command)" ]; then
        printf 'LOOP_GUARD=ok\n'
        printf 'GUIDANCE=Complete NEXT_ACTION using NEXT_SKILL, validate the artifacts, then reconcile.\n'
    else
        printf 'LOOP_GUARD=ok\n'
        printf 'GUIDANCE=Execute only NEXT_COMMAND, then update state and checkpoint.\n'
    fi
}

resume_full() {
    cat "$STATE/migration.yaml"
    printf '\n--- current-context.md ---\n'
    cat "$STATE/current-context.md"

    local unit
    local checkpoint
    local manifest
    unit="$(state_value current_unit)"
    checkpoint="$(state_value last_checkpoint)"

    for manifest in "$STATE/plans/units/"*-slices.yaml; do
        [ -f "$manifest" ] || continue
        printf '\n--- plans/units/%s ---\n' "$(basename "$manifest")"
        cat "$manifest"
    done

    if [ "$unit" != "none" ] && [ -f "$STATE/plans/units/$unit.md" ]; then
        printf '\n--- plans/units/%s.md ---\n' "$unit"
        cat "$STATE/plans/units/$unit.md"
    fi
    if [ "$unit" != "none" ] && [ -f "$STATE/contracts/units/$unit.yaml" ]; then
        printf '\n--- contracts/units/%s.yaml ---\n' "$unit"
        cat "$STATE/contracts/units/$unit.yaml"
    fi
    if [ "$unit" != "none" ] && [ -f "$STATE/plans/units/$unit-model.yaml" ]; then
        printf '\n--- plans/units/%s-model.yaml ---\n' "$unit"
        cat "$STATE/plans/units/$unit-model.yaml"
    fi
    if [ "$checkpoint" != "none" ] && [ -f "$STATE/$checkpoint" ]; then
        printf '\n--- %s ---\n' "$checkpoint"
        cat "$STATE/$checkpoint"
    fi
}

resume() {
    require_workspace
    local mode="$MODE"
    if [ -z "$mode" ]; then
        case "$TITLE" in
            --brief|--full) mode="$TITLE" ;;
            *) mode="--brief" ;;
        esac
    fi

    case "$mode" in
        --full)
            resume_full
            ;;
        --brief)
            resume_brief
            ;;
        *)
            echo "Unknown resume mode: $mode (use --brief or --full)" >&2
            return 2
            ;;
    esac
}

next() {
    require_workspace
    local action="$TITLE"
    local command="$MODE"

    if [ -z "$action" ] || [ "$action" = "checkpoint" ]; then
        echo "Usage: ctrust-state.sh next <project> <action> [command]" >&2
        return 2
    fi

    if [ "$(state_value status)" = "blocked" ]; then
        workflow_run reopen "$action"
    fi

    update_state_line next_action "$action"
    update_state_line next_command "$command"
    update_state_line attempts 0
    update_state_line last_error ""
    update_state_line status in_progress
    sync_context
    printf '{"ts":"%s","event":"next_action_set","action":"%s","command":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$action" "$command" >> "$STATE/logs/progress.ndjson"
    echo "next action recorded"
}

attempt_failed() {
    require_workspace
    local reason="$TITLE"
    local attempts
    local max_attempts
    attempts="$(state_value attempts)"
    max_attempts="$(state_value max_attempts)"
    [ -n "$attempts" ] || attempts=0
    [ -n "$max_attempts" ] || max_attempts=2
    attempts=$((attempts + 1))

    update_state_line attempts "$attempts"
    update_state_line last_error "$reason"

    if [ "$attempts" -ge "$max_attempts" ]; then
        update_state_line status blocked
        sync_context
        printf '{"ts":"%s","event":"attempt_blocked","attempts":%s,"reason":"%s"}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$attempts" "$reason" >> "$STATE/logs/progress.ndjson"
        echo "LOOP_GUARD=stop" >&2
        echo "attempts=$attempts/$max_attempts reason=$reason" >&2
        echo "Stop retrying. Record a blocked checkpoint and ask the user for a decision." >&2
        return 3
    fi

    sync_context
    printf '{"ts":"%s","event":"attempt_failed","attempts":%s,"reason":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$attempts" "$reason" >> "$STATE/logs/progress.ndjson"
    echo "attempts=$attempts/$max_attempts reason=$reason"
}

slugify() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

checkpoint() {
    require_workspace
    local title="$TITLE"
    local slug
    local number
    local filename
    local relative
    local now
    slug="$(slugify "$title")"
    [ -n "$slug" ] || slug="checkpoint"
    number="$(find "$STATE/checkpoints" -maxdepth 1 -type f -name '*.md' | wc -l | tr -d ' ' )"
    number=$((number + 1))
    filename="$(printf '%03d' "$number")-${slug}.md"
    relative="checkpoints/$filename"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    if [ -e "$STATE/$relative" ]; then
        echo "checkpoint already exists: $STATE/$relative" >&2
        return 1
    fi

    cat > "$STATE/$relative" <<EOF
# Checkpoint $(printf '%03d' "$number") — $title

- Created: $now
- Phase: $(state_value phase)
- Unit: $(state_value current_unit)
- Status: $(state_value status)

## Completed

- Describe the behavior already implemented in Rust.

## Incomplete

- Describe the remaining implementation work.

## Modified Files

- path/to/changed/file.rs — describe the change.

## Evidence

- Command and result, for example: cargo test -p crate: passed.
- Reference source or test path: source/file.c:42.

## Known Differences

- Describe intentional or observed differences from the C implementation.

## Risks and Blockers

- None recorded.

## Next Action

- Write one concrete action beginning with a verb.

## Recovery Instructions

1. Read .ctrust/migration.yaml.
2. Read .ctrust/current-context.md.
3. Read the plan for the current unit.
4. Read this checkpoint.
5. Do not repeat completed inventory or analysis.
EOF

    update_state_line last_checkpoint "$relative"
    sync_context
    printf '{"ts":"%s","event":"checkpoint_created","path":"%s","title":"%s"}\n' "$now" "$relative" "$title" >> "$STATE/logs/progress.ndjson"
    echo "created $STATE/$relative"
}

update_state_line() {
    workflow_run update-state "$1" "$2"
}

case "$COMMAND" in
    inventory)
        inventory
        ;;
    recover)
        recover
        ;;
    doctor)
        require_workspace
        workflow_run doctor
        ;;
    behavior-function)
        require_workspace
        workflow_run behavior-function "$TITLE" "$MODE"
        sync_context
        ;;
    init)
        init_workspace
        ;;
    upgrade-state)
        upgrade_state
        ;;
    status)
        status
        ;;
    status-slices)
        status_slices
        ;;
    reconcile)
        reconcile_state
        ;;
    reconcile-artifacts)
        reconcile_artifacts
        ;;
    validate-artifacts)
        validate_artifacts
        ;;
    plan-slices)
        plan_slices
        ;;
    function-map)
        function_map
        ;;
    select-function)
        select_function
        ;;
    record-implementation)
        record_implementation
        ;;
    record-evidence)
        record_evidence
        ;;
    compare)
        compare_function
        ;;
    organize)
        organize
        ;;
    validate-organization)
        validate_organization
        ;;
    verify-slice)
        require_workspace
        slice="${TITLE/checkpoint/}"
        if [ -z "$slice" ]; then
            slice="$(state_value current_unit)"
        fi
        workflow_run guard-action "$slice" verify-slice
        "$SCRIPT_DIR/migration-gate.sh" "$ROOT" "$slice"
        workflow_run verify-slice "$slice"
        checkpoint
        reconcile_state
        ;;
    resume)
        resume
        ;;
    next)
        next
        ;;
    failed)
        attempt_failed
        ;;
    contract)
        contract
        ;;
    model)
        model
        ;;
    checkpoint)
        checkpoint
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        echo "Unknown command: $COMMAND" >&2
        usage
        exit 1
        ;;
esac
