from pathlib import Path
import hashlib
from .models import Artifact, Diagnostic, canonical_root, safe_relative, sha256_bytes

SUPPORTED = {".yaml", ".yml", ".md", ".ndjson", ".json", ".txt"}

class ArtifactReader:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.root = canonical_root(self.project)

    def read(self):
        artifacts = {}
        diagnostics = []
        if not self.root.is_dir():
            diagnostics.append(Diagnostic("workspace_missing", "error", ".ctrust", "The .ctrust directory does not exist", "Run init", True))
            return artifacts, diagnostics
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            relative = safe_relative(self.root, path)
            if relative is None:
                diagnostics.append(Diagnostic("path_escape", "error", str(path), "Artifact escapes .ctrust", "Remove the escaped path", True))
                continue
            try:
                raw = path.read_bytes()
                raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                diagnostics.append(Diagnostic("read_error", "error", relative, str(exc), "Repair or remove the artifact", True))
                continue
            parsed = None
            if path.suffix.lower() in {".yaml", ".yml"}:
                from workflow import load, WorkflowError
                try:
                    parsed = load(path)
                except WorkflowError as exc:
                    diagnostics.append(Diagnostic("invalid_yaml", "error", relative, str(exc), "Repair YAML", True))
            artifacts[relative] = Artifact(relative, path.suffix.lower().lstrip("."), raw, sha256_bytes(raw), parsed)
        return artifacts, diagnostics
