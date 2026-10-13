# Migration Slice: SLICE_ID

- Unit: UNIT_NAME
- Order: 1
- Status: planned
- Objective: describe one business behavior

## Source C

- Project root:
- Input headers (`.h`) — required:
- Functions to port:

| C function | C declaration | Rust name | Rust target | Status |
|---|---|---|---|---|
| `FUNCTION_NAME` | `source/header.h:1` | `rust_function_name` | `crate::module::rust_function_name` | planned |

- Files:
- Includes:
- Callers:
- Call graph evidence: `.ctrust/inventory/call-graph.yaml`
- Call graph status: `generated` | `unavailable` | `incomplete` | `not-applicable`
- Direct callees required by this slice:
- Unresolved or indirect calls:
## Dependencies and implementation order

- Required previous slices:
- Dependency relation: `calls` | `requires` | `shares-contract` | `replaces`
- Blocking dependency status: `verified` | `completed`
- Why this slice cannot be implemented before its dependencies:
- Cycles or strongly connected functions requiring one unit:


- Include:
- Exclude:
- Shared dependencies:
- Infrastructure to port:
- Infrastructure to adapt:
- Infrastructure to simulate:
- Infrastructure to exclude:
- Adapters/interfaces:

## Organization

- Organization schema: 1
- Naming rule: semantic-v1
- Semantic name:
- Semantic source:
- Implementation file: `src/<slice-name>.rs`
- Unit tests: inline in `src/<slice-name>.rs` under `#[cfg(test)]`
- Integration test file: `tests/<slice-name>_integration.rs`
- Integration helpers: `tests/common/mod.rs` when shared helpers are needed
- Module path: `crate::<slice-name>`
- Crate-relative: yes
- Shared type references: `src/type.rs`
- Public reexports:
- Organization status: planned

- Workspace root: `target.project_root`
- Cargo manifest: `target.project_root/Cargo.toml`
- Crate path: `target.path`
- Crate name:
- Workspace member: `true`
- Output kind: `library` | `binary`
- Library kind or binary name:
- Public API or entry point:

## Slice cycle

1. **Behavior** — `.ctrust/inventory/slices/SLICE_ID/behavior.yaml`; confirm the observed C behavior before extracting the contract.
2. **Contract** — `.ctrust/contracts/units/SLICE_ID.yaml`
3. **Model** — `.ctrust/plans/units/SLICE_ID-model.yaml`
4. **Organization** — define `rust_file`, `module_path` e a árvore de módulos; mantenha `lib.rs`/`main.rs` fino.
5. **Port** — implement only this slice in the target Rust path.
6. **Verify** — run characterization, Rust tests and equivalence checks.

## Prerequisites

- Previous slices:
- External dependencies:

## Completion criteria

- [ ] contract confirmed
- [ ] Rust model complete
- [ ] implementation complete and not a placeholder/default-return stub
- [ ] every ported function has a verified `ctrust:source` comment
- [ ] characterization tests cover normal, boundary, error and invalid-resource cases
- [ ] Rust outputs are compared with C outputs or a versioned reference fixture
- Arquivo Rust: `crates/domain/src/parser.rs`
- Módulo Rust: `crate::parser::parse_token`
- `lib.rs`/`main.rs` deve apenas declarar o módulo e reexportar a API.
- [ ] each origin comment contains file, line, symbol, slice and contract
- [ ] verification passed
- [ ] differences and risks recorded
- [ ] checkpoint created with one next action
