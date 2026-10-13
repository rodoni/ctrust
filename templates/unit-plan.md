# Migration Unit: UNIT_NAME

## Scope

### Business logic

- Business behavior to extract:
- Rules, calculations or validations:
- Public inputs and outputs:
- Expected Rust consumers:

### Source C

- Project root:
- Component or slice:
- C sources:
- Headers:
- Entry points:
- Callers:
- Dependencies:

### Selection boundaries

- Include files/symbols:
- Exclude files/symbols:
- Shared dependencies:
- Non-portable infrastructure: shared memory, IPC, hardware, UI, CLI, platform APIs
- Classification: port | adapt | simulate | exclude
- Adapters/interfaces required:

### Target Rust

- Workspace root: `target.project_root`
- Cargo manifest: `target.project_root/Cargo.toml`
- Crate/module path: `target.path`
- Workspace members:
- Product kind: `library` | `binary`
- Library kind (`rlib`, `cdylib`, `staticlib`) or binary name:
- Domain library API or binary entry point:

## Slices de migração

- Slice manifest: `.ctrust/plans/units/UNIT_NAME-slices.yaml`
- Slice plan: `.ctrust/plans/units/SLICE_ID.md`
- Slice inventory: `.ctrust/inventory/slices/SLICE_ID/`
- Slice behavior: `.ctrust/inventory/slices/SLICE_ID/behavior.yaml`
- Slice contract: `.ctrust/contracts/units/SLICE_ID.yaml`
- Slice model: `.ctrust/plans/units/SLICE_ID-model.yaml`

Cada slice percorre individualmente:

```text
contract → model → port → verify → checkpoint
```

O slice seguinte só começa quando o anterior atingir seu critério de conclusão ou for explicitamente marcado como `blocked` com uma próxima ação registrada.
## Contract gate

- Contract file: `.ctrust/contracts/units/UNIT_NAME.yaml`
- Critical unknowns:
- Characterization tests:
- Gate to pass:

## Modeling gate

- Model file: `.ctrust/plans/units/UNIT_NAME-model.yaml`
- Rust types:
- Public API or binary entry point:
- Ownership:
- Error strategy:
- Output kind: `library` | `binary`
- Unsafe/FFI decision:
- Completion criteria:

## Status

- phase: inventory
- status: planned
- next_action: fill scope and run contract extraction
