"""Deterministic function-level migration recovery core for ctrust."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class UnitState(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    TESTED = "tested"
    VERIFIED = "verified"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class FunctionRecord:
    function_id: str
    c_name: str
    slice_id: str
    declaration_file: str = ""
    declaration_line: Optional[int] = None
    definition_file: str = ""
    definition_line: Optional[int] = None
    rust_name: str = ""
    rust_file: str = ""
    module_path: str = ""
    state: UnitState = UnitState.PLANNED
    implementation_status: str = "pending"
    test_status: str = "pending"
    comparison_status: str = "pending"
    legacy_reference_available: bool = False
    legacy_tests_available: bool = False
    executable_harness_available: bool = False
    reference_reason: str = ""
    verification_scope: str = "pending"
    unit_id: str = ""


@dataclass(frozen=True)
class FunctionUnit:
    unit_id: str
    slice_id: str
    function_ids: Tuple[str, ...]
    selection: str = "single_function"
    grouping_reason: str = ""
    context_cost: int = 0
    context_limit: int = 100
    state: UnitState = UnitState.PLANNED
    attempts: int = 0
    last_checkpoint: str = ""
    legacy_reference_available: bool = False
    legacy_tests_available: bool = False
    executable_harness_available: bool = False
    reference_reason: str = ""
    verification_scope: str = "pending"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    owner: str = ""
    blocking: bool = True


@dataclass(frozen=True)
class SelectionResult:
    unit: Optional[FunctionUnit]
    diagnostics: Tuple[Diagnostic, ...] = ()


@dataclass
class FunctionMap:
    slice_id: str
    revision: int = 1
    functions: Dict[str, FunctionRecord] = field(default_factory=dict)
    units: Dict[str, FunctionUnit] = field(default_factory=dict)
    next_function: str = ""

    def validate(self) -> List[Diagnostic]:
        diagnostics: List[Diagnostic] = []
        for function_id, record in self.functions.items():
            if function_id != record.function_id:
                diagnostics.append(Diagnostic("function_id_mismatch", "Function map key differs from record", function_id))
            for field_name in ("c_name", "slice_id", "definition_file"):
                if not getattr(record, field_name):
                    diagnostics.append(Diagnostic("mapping_incomplete", f"Missing {field_name}", function_id))
            if record.slice_id != self.slice_id:
                diagnostics.append(Diagnostic("slice_mismatch", "Function belongs to another slice", function_id))
            if record.state == UnitState.VERIFIED and not record.rust_file and not record.rust_name:
                diagnostics.append(Diagnostic("verified_without_destination", "Verified function has no Rust destination", function_id))
            if record.comparison_status == "verified_without_legacy_reference":
                if record.legacy_reference_available or record.legacy_tests_available or record.executable_harness_available:
                    diagnostics.append(Diagnostic("inconsistent_reference_status", "Verified without legacy reference but a reference is marked available", function_id))
                if not record.reference_reason:
                    diagnostics.append(Diagnostic("missing_reference_reason", "Missing reason for unavailable legacy reference", function_id))
                if record.verification_scope != "behavior_contract":
                    diagnostics.append(Diagnostic("invalid_verification_scope", "Expected behavior_contract scope", function_id))
            if record.comparison_status == "parity_verified" and not record.executable_harness_available:
                diagnostics.append(Diagnostic("parity_without_reference", "Parity cannot be verified without executable legacy reference", function_id))
        for unit_id, unit in self.units.items():
            if unit_id != unit.unit_id:
                diagnostics.append(Diagnostic("unit_id_mismatch", "Unit map key differs from record", unit_id))
            if not unit.function_ids:
                diagnostics.append(Diagnostic("empty_function_unit", "Function unit has no members", unit_id))
            for function_id in unit.function_ids:
                if function_id not in self.functions:
                    diagnostics.append(Diagnostic("unknown_function", "Unit references unknown function", unit_id))
                elif self.functions[function_id].unit_id not in ("", unit_id):
                    diagnostics.append(Diagnostic("multiple_units", "Function is assigned to multiple units", function_id))
        return diagnostics

    def select_next(self, context_limit: int = 100) -> SelectionResult:
        diagnostics = self.validate()
        if any(item.blocking for item in diagnostics):
            return SelectionResult(None, tuple(diagnostics))
        candidates = [record for record in self.functions.values() if record.state == UnitState.PLANNED]
        candidates.sort(key=lambda item: (item.definition_line is None, item.definition_line or 0, item.function_id))
        for record in candidates:
            if record.unit_id and record.unit_id in self.units:
                continue
            unit_id = f"{self.slice_id}-fu-{len(self.units) + 1:03d}"
            unit = FunctionUnit(unit_id, self.slice_id, (record.function_id,), context_cost=1, context_limit=context_limit)
            return SelectionResult(unit)
        return SelectionResult(None, (Diagnostic("no_eligible_function", "No planned function is eligible", self.slice_id, False),))


ALLOWED_TRANSITIONS = {
    UnitState.PLANNED: {UnitState.IN_PROGRESS},
    UnitState.IN_PROGRESS: {UnitState.IMPLEMENTED, UnitState.BLOCKED},
    UnitState.IMPLEMENTED: {UnitState.TESTED, UnitState.BLOCKED},
    UnitState.TESTED: {UnitState.VERIFIED, UnitState.BLOCKED},
    UnitState.BLOCKED: {UnitState.IN_PROGRESS, UnitState.IMPLEMENTED, UnitState.TESTED},
    UnitState.VERIFIED: set(),
}


def transition(function_map: FunctionMap, unit_id: str, target: UnitState) -> List[Diagnostic]:
    unit = function_map.units.get(unit_id)
    if unit is None:
        return [Diagnostic("unknown_unit", "Function unit does not exist", unit_id)]
    if target not in ALLOWED_TRANSITIONS[unit.state]:
        return [Diagnostic("invalid_transition", f"Cannot transition {unit.state.value} to {target.value}", unit_id)]
    return []
