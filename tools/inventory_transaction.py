"""Publish a complete inventory generation without exposing partial files.

On Linux, renameat2(RENAME_EXCHANGE) swaps existing directories atomically.
An unsupported filesystem/platform fails before replacing the live inventory.
Transactions and previous generations are retained beside the slice directory.
"""
import ctypes
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from artifact_sync.transaction import TransactionError, atomic_write


@contextmanager
def inventory_lock(parent):
    # flock is released by the kernel even when the exporter is killed.
    with (parent / ".inventory.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TransactionError(f"Inventory publication already running: {parent}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def exchange_directories(left, right):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise TransactionError("Atomic directory exchange unavailable; live inventory preserved")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise TransactionError(f"Atomic inventory exchange failed ({os.strerror(error)}); live inventory preserved")


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reviewed_artifacts(output):
    # Reuse the controller's duplicate-key-safe YAML loader.
    from workflow import load
    protected = []
    manifest = output / "manifest.yaml"
    metadata = load(manifest) if manifest.is_file() else {}
    digests = metadata.get("artifact_digests", {})
    if output.exists() and any(output.iterdir()) and (not metadata.get("generation") or not isinstance(digests, dict) or not digests):
        return ["legacy inventory without generation fingerprints; review before replacement"]
    for name, digest in digests.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise TransactionError(f"Invalid inventory fingerprint path: {name}")
        path = output / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            protected.append(name)
    for name in ("data-structures.yaml", "mapping-confirmation.yaml", "behavior.yaml", "function-map.yaml"):
        path = output / name
        if not path.exists():
            continue
        data = load(path)
        progressed = data.get("status") == "confirmed" or bool(data.get("evidence"))
        functions = data.get("functions", [])
        if not isinstance(functions, list) or any(not isinstance(fn, dict) for fn in functions):
            protected.append(name)
            continue
        progressed = progressed or any(fn.get("state", "planned") not in {"planned", "blocked"} or fn.get("evidence_refs") for fn in functions)
        if progressed and name not in protected:
            protected.append(name)
    return protected


def reconcile_transactions(parent):
    """Resolve journals left by a killed process using the live generation ID."""
    from workflow import load
    for path in (parent / ".inventory-transactions").glob("*/transaction.json"):
        journal = json.loads(path.read_text())
        if journal.get("status") not in {"preparing", "ready"}:
            continue
        output = Path(journal["output"])
        if output.parent != parent:
            raise TransactionError(f"Transaction output escapes inventory parent: {path}")
        manifest = output / "manifest.yaml"
        published = manifest.is_file() and load(manifest).get("generation") == journal["generation"]
        journal["status"] = "published" if published else "abandoned"
        atomic_write(path.parent, path.name, json.dumps(journal, indent=2).encode())


def publish_inventory(output, slice_id, generate, replace_reviewed=False):
    """Generate and validate in staging, then atomically publish; keep old data.

    generate(stage, generation) writes the complete nine-artifact bundle.
    A process killed before publication leaves the live inventory untouched.
    A process killed after exchange leaves the old generation in snapshot/.
    """
    from workflow import inventory_errors
    output = Path(output).absolute()
    if output.is_symlink():
        raise TransactionError(f"Inventory output must be a directory, not a symlink: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with inventory_lock(output.parent):
        reconcile_transactions(output.parent)
        if output.exists() and not output.is_dir():
            raise TransactionError(f"Inventory output is not a directory: {output}")
        protected = reviewed_artifacts(output) if output.exists() and not replace_reviewed else []
        if protected and not replace_reviewed:
            raise TransactionError("Reviewed inventory preserved: " + ", ".join(protected) + "; review inputs and explicitly use --replace-reviewed to archive and replace it")
        generation = uuid.uuid4().hex
        transaction = output.parent / ".inventory-transactions" / generation
        stage = transaction / "snapshot"
        stage.mkdir(parents=True)
        journal = {"version": 1, "generation": generation, "slice": slice_id, "output": str(output), "status": "preparing", "previous_snapshot": str(stage)}
        def record(status):
            journal["status"] = status
            atomic_write(transaction, "transaction.json", json.dumps(journal, indent=2).encode())
        record("preparing")
        try:
            if output.exists():
                shutil.copytree(output, stage, dirs_exist_ok=True)
            generate(stage, generation)
            # inventory_errors normally locates a slice below a .ctrust root.
            errors = inventory_errors(None, slice_id, directory=stage)
            if errors:
                raise TransactionError("Staged inventory invalid: " + "; ".join(errors))
            for path in stage.rglob("*"):
                if path.is_file():
                    with path.open("rb") as handle:
                        os.fsync(handle.fileno())
            fsync_directory(stage)
            record("ready")
            if output.exists():
                exchange_directories(stage, output)
            else:
                os.rename(stage, output)
            fsync_directory(output.parent)
            fsync_directory(transaction)
            record("published")
        except BaseException:
            # Publication may already have succeeded: inspect the generation
            # rather than claim rollback when fsync/journaling fails afterwards.
            from workflow import load
            try:
                manifest = output / "manifest.yaml"
                published = manifest.is_file() and load(manifest).get("generation") == generation
                record("published" if published else "failed")
            except (ValueError, OSError, TransactionError):
                # Preserve the original error if the filesystem cannot journal
                # it. The next exporter resolves an unfinished ready journal.
                pass
            raise
        return generation
