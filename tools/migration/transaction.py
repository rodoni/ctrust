from pathlib import Path
import json
import fcntl
import os
import socket
import tempfile
import time

class TransactionError(RuntimeError):
    pass

class WorkspaceLock:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock_dir = self.root / ".locks"
        self.path = self.lock_dir / "migration.lock"
        self.fd = None

    def __enter__(self):
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Keep the lock inode stable. The kernel releases flock on crashes;
            # file existence is not evidence that an operation is still alive.
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.fd, 0)
            payload = {"pid": os.getpid(), "host": socket.gethostname(), "started": time.time(), "root": str(self.root)}
            os.write(self.fd, json.dumps(payload).encode())
            os.fsync(self.fd)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise TransactionError(f"lock occupied: {self.path}") from exc
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TransactionError(f"destination_write_attempt: {relative}") from exc
    return path

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
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
