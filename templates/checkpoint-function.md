---
checkpoint_id: CP_ID
slice_id: SLICE_ID
unit_id: UNIT_ID
function_ids: []
unit_state: tested
function_map_revision: 1
source_mapping_revision: 1
last_valid_state: implemented
evidence_refs: []
rollback_boundary: UNIT_ID
next_function: ""
next_action: ""
next_command: ""
---

# Function Checkpoint: UNIT_ID

## Completed

- Record the behavior implemented for this function unit.

## Evidence

- Normal:
- Boundary:
- Error:
- C/Rust comparison:

## Known Differences

- None recorded.

## Recovery

- Read `migration.yaml`, `current-context.md`, the active function map and this checkpoint.
- Do not repeat completed work.
