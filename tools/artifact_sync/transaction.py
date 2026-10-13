from pathlib import Path
import json
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from .models import Diagnostic, canonical_root

class TransactionError(RuntimeError):
    pass

class WorkspaceLock:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock_dir = self.root / ".locks"
        self.path = self.lock_dir / "reconcile.lock"
        self.fd = None

    def __enter__(self):
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = {"pid": os.getpid(), "host": socket.gethostname(), "started": time.time(), "root": str(self.root)}
            os.write(self.fd, json.dumps(payload).encode())
            os.fsync(self.fd)
        except FileExistsError as exc:
            raise TransactionError(f"lock occupied: {self.path}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TransactionError(f"destination_write_attempt: {relative}") from exc
    return path

@contextmanager
def staging(root: Path, transaction_id: str):
    root = root.resolve()
    staging_root = root / ".staging" / transaction_id
    staging_root.mkdir(parents=True, exist_ok=False)
    try:
        yield staging_root
    finally:
        import shutil
        shutil.rmtree(staging_root, ignore_errors=True)


def atomic_write(root: Path, relative: str, content: bytes):
    target = safe_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
