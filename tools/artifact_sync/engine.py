from pathlib import Path
import hashlib
import json
from .models import Diagnostic, ReconciliationResult
from .reader import ArtifactReader
from .transaction import WorkspaceLock, TransactionError, atomic_write

def _managed_block(path, content, start, end):
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    begin = text.find(start)
    finish = text.find(end)
    if begin >= 0 and finish >= 0 and finish > begin:
        finish += len(end)
        if text[finish:finish + 1] == "\n" and content.endswith("\n"):
            finish += 1
        return text[:begin] + content + text[finish:]
    if begin >= 0 or finish >= 0:
        raise ValueError("managed markers are malformed")
    separator = "\n" if text and not text.endswith("\n") else ""
    return text + separator + content

def reconcile(project, dry_run=False, migrate_legacy=False):
    from workflow import load, manifests, WorkflowError
    project = Path(project).resolve()
    reader = ArtifactReader(project)
    artifacts, diagnostics = reader.read()
    result = ReconciliationResult(diagnostics=list(diagnostics), input_digest=hashlib.sha256("".join(k+v.checksum for k,v in sorted(artifacts.items())).encode()).hexdigest())
    candidates = {}
    try:
        state = load(reader.root / "migration.yaml")
        if state.get("version") != 2:
            raise WorkflowError("Run upgrade-state before reconciling legacy migration state")
        entries = manifests(reader.root)
        result.selected_slice = state.get("current_unit", "")
        if result.selected_slice not in entries:
            result.selected_slice = ""
        result.next_action = state.get("next_action", "")
        for sid, (item, _, _) in entries.items():
            relative = f"plans/units/{sid}.md"
            path = reader.root / relative
            active = sid == result.selected_slice
            start = "<!-- ctrust:managed:slice-state:v1:start -->"
            end = "<!-- ctrust:managed:slice-state:v1:end -->"
            block = f"{start}\n## CTrust State (managed)\n- Status: {item.get('status', 'planned')}\n- Phase: {state.get('phase', 'inventory') if active else 'waiting'}\n- Active slice: {result.selected_slice or 'none'}\n- Next action: {result.next_action if active else 'Wait for slice selection'}\n{end}\n"
            candidate = _managed_block(path, block, start, end)
            previous = path.read_text() if path.exists() else ""
            if candidate != previous:
                result.changed.append(relative)
                candidates[relative] = candidate.encode()
            else:
                result.unchanged.append(relative)
    except (WorkflowError, ValueError) as exc:
        result.status = "invalid"
        result.diagnostics.append(Diagnostic("artifact_invalid", "error", ".ctrust", str(exc), "Repair the artifact before reconciling", True))
    if any(d.blocking for d in result.diagnostics):
        result.status = "invalid"
    if not dry_run and result.status == "valid" and candidates:
        import uuid
        transaction_id = uuid.uuid4().hex
        try:
            with WorkspaceLock(reader.root):
                for relative, content in candidates.items():
                    atomic_write(reader.root, relative, content)
                audit = reader.root / "logs/audit.ndjson"
                audit.parent.mkdir(parents=True, exist_ok=True)
                with audit.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"schema": 1, "event": "transaction_committed", "transaction_id": transaction_id, "changed": result.changed}) + "\n")
                result.committed = True
        except TransactionError as exc:
            result.status = "conflict"
            result.diagnostics.append(Diagnostic("transaction_failed", "error", ".locks/reconcile.lock", str(exc), "Resolve lock", True))
    return result

def validate(project):
    return reconcile(project, dry_run=True)
