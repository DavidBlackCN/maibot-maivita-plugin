"""Read-only inspection, WAL-consistent snapshots and exclusive runtime ownership.

No import of GoalDatabase: its constructor writes metadata. Never auto-discover
production files; callers supply candidate paths explicitly.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
import os
import sqlite3
import time


def absolute_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("data_dir must be an absolute path")
    return path.resolve()


def resolve_data_dir(ctx, configured: str = "") -> Path:
    supported = getattr(getattr(ctx, "paths", None), "data_dir", None)
    if supported:
        return absolute_dir(supported)
    if configured:
        return absolute_dir(configured)
    raise ValueError("No SDK ctx.paths.data_dir; configure absolute storage.data_dir")


def discover(candidates: list[Path]) -> dict:
    found = sorted({str(p.resolve()) for p in candidates if p.is_file()})
    return {"candidates": found, "status": "ambiguous" if len(found) > 1 else
            "discovered" if found else "new_install", "selected": found[0] if len(found) == 1 else None}


def readonly(path: Path, *, offline=False, allow_sidecars=False):
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as file:
        header = file.read(20)
    wal, shm = Path(str(path) + "-wal"), Path(str(path) + "-shm")
    options = "?mode=ro"
    if offline:
        # Explicit operator assertion: writer stopped, checkpointed, no WAL.
        # Never silently ignore a WAL, even with this flag.
        if wal.exists():
            raise ValueError("offline reading refuses existing WAL; stop/checkpoint first")
        options += "&immutable=1"
    elif header[18:20] == b"\x02\x02" and not (wal.is_file() and shm.is_file()) and not allow_sidecars:
        raise ValueError("WAL sidecars absent; refusing source-directory writes. Use --offline only after stopping/checkpointing writer")
    return sqlite3.connect(path.resolve().as_uri() + options, uri=True, timeout=5)


GOAL_COLUMNS = {
    "goal_id", "name", "description", "goal_type", "priority", "creator_id", "chat_id",
    "status", "created_at", "deadline", "interval_seconds", "conditions", "parameters",
    "progress", "last_executed_at", "execution_count", "updated_at",
}


def inspect_database(path: Path, *, offline=False, allow_sidecars=False) -> dict:
    with closing(readonly(path, offline=offline, allow_sidecars=allow_sidecars)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(goals)")}
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone() if "metadata" in tables else None
        version = version[0] if version else None
        valid = integrity == ["ok"] and version == "1" and columns == GOAL_COLUMNS and tables == {"goals", "metadata"}
        counts = dict(conn.execute("SELECT goal_type, COUNT(*) FROM goals GROUP BY goal_type")) if "goals" in tables else {}
        return {"path": str(path.resolve()), "integrity": integrity, "schema_version": version,
                "tables": sorted(tables), "columns": sorted(columns), "counts": counts,
                "valid_legacy_schema": valid,
                "status": "validated" if valid else "blocked_unknown_or_damaged_schema"}


def fingerprint(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def source_manifest(source: Path) -> list[dict]:
    return [{"path": str(path), "bytes": path.stat().st_size,
             "sha256": fingerprint(path), "sample_only": True}
            for path in [source, Path(str(source) + "-wal")] if path.is_file()]


def snapshot(source: Path, destination: Path, *, offline=False) -> dict:
    """Create a new verified backup, never replacing an existing destination."""
    source, destination = source.resolve(), destination.resolve()
    if source.parent == destination.parent:
        raise ValueError("snapshot must be outside source directory")
    if not inspect_database(source, offline=offline)["valid_legacy_schema"]:
        raise ValueError("unsupported/damaged source schema; source left unchanged")
    source_files = source_manifest(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL also rejects existing symlinks. Failed artifacts remain for diagnosis.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    deadline = time.monotonic() + 15
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("snapshot timed out; no activation")
    with closing(readonly(source, offline=offline)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst, pages=128, progress=progress, sleep=0.05)
        # Portable self-contained artifact; only the NEW destination is changed.
        dst.execute("PRAGMA journal_mode=DELETE")
    report = inspect_database(destination)
    if not report["valid_legacy_schema"]:
        raise ValueError("snapshot validation failed; no activation")
    return {"status": "backed_up", "source": str(source), "snapshot": str(destination),
            "source_files": source_files, "snapshot_sha256": fingerprint(destination), "validation": report}


def restore(source: Path, destination: Path, expected_sha256: str) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source.parent == destination.parent:
        raise ValueError("restore must be outside source directory")
    if fingerprint(source) != expected_sha256:
        raise ValueError("snapshot checksum mismatch")
    if not inspect_database(source, offline=True)["valid_legacy_schema"]:
        raise ValueError("unsupported snapshot schema")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Copy only a checksum-verified, self-contained offline snapshot. Re-running
    # SQLite backup can alter its header counters, so restoration preserves bytes.
    import shutil
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if fingerprint(destination) != expected_sha256 or fingerprint(source) != expected_sha256:
        raise ValueError("restore checksum mismatch; no activation")
    validation = inspect_database(destination, offline=True)
    if not validation["valid_legacy_schema"]:
        raise ValueError("restored snapshot invalid; no activation")
    return {"status": "restored_R0_snapshot", "source": str(source), "snapshot": str(destination),
            "snapshot_sha256": fingerprint(destination), "validation": validation}


class WriterLease:
    """OS lock released on crash. A persistent empty lock file is harmless.

    The lease guards a deployment-wide operator-specified path, so separate DB
    directories still cannot host two MaiVita engines. Unmodified old plugins do
    not participate; the host registry must reject those before acquisition.
    """
    def __init__(self, path: Path):
        self.path = absolute_dir(path)
        self.file = None

    def acquire(self):
        if self.file is not None:
            raise RuntimeError("writer lease already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            file.seek(0, 2)
            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            file.close()
            raise RuntimeError("another life writer is active") from exc
        self.file = file

    def release(self):
        if self.file is not None:
            self.file.close()
            self.file = None
