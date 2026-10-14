"""Ordered slice stages and the prerequisites of every advancing command.

Persisted phase labels are projections. Validated artifacts determine which
stage is eligible; changing a phase label cannot bypass these prerequisites.
Function transitions remain governed by function_recovery.UnitState.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    phase: str
    skill: str
    action: str
    recovery_code: str


STAGES = (
    Stage("inventory", "c-code-inventory", "Complete inventory", "inventory_incomplete"),
    Stage("data_structure_mapping", "data-structure-mapping", "Confirm structures and mapping", "structures_unconfirmed"),
    Stage("behavior_extraction", "c-behavior-extraction", "Confirm behavior", "behavior_unconfirmed"),
    Stage("contract_analysis", "contract-extraction", "Complete and confirm contract", "contract_unconfirmed"),
    Stage("modeling", "rust-domain-modeling", "Complete and confirm model", "model_unconfirmed"),
    Stage("organization", "rust-module-organization", "Generate and validate organization", "organization_stale"),
)

# Number of consecutive validated stages required before executing a command.
ACTION_REQUIREMENTS = {
    "inventory": 0,
    "function-map": 0,  # Explicit legacy-map repair; does not advance a slice.
    "contract": 3,
    "model": 4,
    "organize": 5,
    "select-function": 6,
    "record-implementation": 6,
    "record-evidence": 6,
    "compare": 6,
    "verify-slice": 6,
}

FUNCTION_ACTIONS = {
    "function_recovery": ["select-function"],
    "function_implementation": ["record-implementation"],
    "function_testing": ["record-evidence"],
    "function_comparison": ["compare"],
    "verification": ["verify-slice"],
}
