from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

CrateKind = Literal["library", "binary"]

@dataclass(frozen=True)
class FunctionInput:
    c_name: str
    rust_name: str = ""

@dataclass(frozen=True)
class SliceInput:
    slice_id: str
    objective: str = ""
    behavior_summary: str = ""
    contract_name: str = ""
    functions: tuple[FunctionInput, ...] = ()
    test_cases: tuple[str, ...] = ()
    name_override: Optional[str] = None

@dataclass(frozen=True)
class OrganizationInput:
    crate_kind: CrateKind
    crate_root: str
    unit: str
    slices: tuple[SliceInput, ...]
    shared_types: tuple[str, ...] = ()
    registered_reexports: tuple[str, ...] = ()
    persisted_mapping: Optional[Dict[str, Any]] = None
    schema_version: int = 1
    naming_rule_version: str = "semantic-v1"

@dataclass(frozen=True)
class Diagnostic:
    rule: str
    owner: str
    expected: str
    observed: str
    source_inputs: tuple[str, ...] = ()
    remediation: str = ""
    blocking: bool = True

@dataclass(frozen=True)
class SliceOrganization:
    slice_id: str
    semantic_name: str
    semantic_source: str
    implementation_file: str
    unit_test_file: str
    integration_test_file: str
    test_file: str
    module_path: str
    functions: tuple[str, ...]
    test_cases: tuple[str, ...]
    naming_rule: str

@dataclass(frozen=True)
class OrganizationPlan:
    schema_version: int
    organization_schema: int
    naming_rule_version: str
    status: str
    unit: str
    crate_root: str
    crate_kind: CrateKind
    entry_file: str
    shared_type_file: str
    slices: tuple[SliceOrganization, ...]
    errors: tuple[Diagnostic, ...] = ()

@dataclass(frozen=True)
class OrganizationError(Exception):
    diagnostic: Diagnostic

    def __str__(self) -> str:
        return f"{self.diagnostic.rule}: {self.diagnostic.observed}"
