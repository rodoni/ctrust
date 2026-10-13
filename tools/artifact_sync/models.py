from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import hashlib

@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    path: str
    message: str
    action: str = ""
    blocking: bool = True

@dataclass(frozen=True)
class Artifact:
    path: str
    kind: str
    raw: bytes
    checksum: str
    parsed: Any = None

@dataclass
class ReconciliationResult:
    status: str = "valid"
    changed: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
    conflicts: List[Diagnostic] = field(default_factory=list)
    selected_slice: str = ""
    next_action: str = ""
    committed: bool = False
    input_digest: str = ""

    def as_dict(self):
        return {
            "status": self.status,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "diagnostics": [d.__dict__ for d in self.diagnostics],
            "conflicts": [d.__dict__ for d in self.conflicts],
            "selected_slice": self.selected_slice,
            "next_action": self.next_action,
            "committed": self.committed,
            "input_digest": self.input_digest,
        }

def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def canonical_root(project: Path) -> Path:
    return (project / ".ctrust").resolve()

def safe_relative(root: Path, path: Path) -> Optional[str]:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return None
