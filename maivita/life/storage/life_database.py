"""Isolated R1 database API. Nothing here is wired to the R0 plugin lifecycle.

Creation/migration are explicit. Normal open refuses old/newer/drifted schemas.
The short transaction boundary validates cross-table domain references before
commit; command receipts/idempotency live in LifeStateEngine and dispatch in events.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import os
import sqlite3
import time

from ..domain.clock import Clock, SystemClock
from ..domain.constraints import validate_aggregate
from .repositories import Repository, SPECS
from .schema import SchemaError, migrate, validate_schema
from .safety import fingerprint


def connect(path: Path, *, busy_timeout_ms: int = 5000):
    if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 60000:
        raise ValueError("busy_timeout_ms must be an integer in 0..60000")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True,
                           isolation_level=None, timeout=busy_timeout_ms / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


class LifeDatabase:
    def __init__(self, path: Path, *, clock: Clock | None = None, busy_timeout_ms: int = 5000):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError("R1 database path must be absolute")
        self.path = path.resolve()
        self.clock = clock if clock is not None else SystemClock()
        self.busy_timeout_ms = busy_timeout_ms
        with closing(connect(self.path, busy_timeout_ms=busy_timeout_ms)) as conn:
            validate_schema(conn)

    @classmethod
    def create(cls, path: Path, *, clock: Clock | None = None, busy_timeout_ms: int = 5000):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError("explicit absolute database path required")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never adopt/overwrite an old goals.db or existing new database.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        actual_clock = clock if clock is not None else SystemClock()
        with closing(connect(path, busy_timeout_ms=busy_timeout_ms)) as conn:
            migrate(conn, actual_clock)
            conn.execute("PRAGMA journal_mode=WAL")
        return cls(path, clock=actual_clock, busy_timeout_ms=busy_timeout_ms)

    def unit_of_work(self):
        return UnitOfWork(self)

    def read_view(self):
        """Consistent read snapshot without acquiring the sole writer lock."""
        return UnitOfWork(self, read_only=True)

    def backup(self, destination: Path):
        destination = Path(destination)
        if not destination.is_absolute():
            raise ValueError("explicit absolute backup path required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        deadline = time.monotonic() + 15
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError("new-schema backup timed out")
        with closing(connect(self.path, busy_timeout_ms=self.busy_timeout_ms)) as src, closing(sqlite3.connect(destination)) as dst:
            validate_schema(src)
            src.backup(dst, pages=128, progress=progress, sleep=0.05)
            dst.execute("PRAGMA journal_mode=DELETE")
        with closing(connect(destination)) as conn:
            version = validate_schema(conn)
        return {"schema_version": version, "snapshot": str(destination.resolve()), "sha256": fingerprint(destination),
                "integrity": "ok", "activated": False}


class UnitOfWork:
    def __init__(self, database: LifeDatabase, *, read_only=False):
        self.database = database
        self.read_only = read_only
        self.connection = None
        self._finished = False
        self.failed = False
        for name, spec in SPECS.items():
            setattr(self, name, Repository(self, spec))

    def __enter__(self):
        if self.connection is not None or self._finished:
            raise RuntimeError("UnitOfWork cannot be reused")
        conn = connect(self.database.path, busy_timeout_ms=self.database.busy_timeout_ms)
        try:
            if self.read_only:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN" if self.read_only else "BEGIN IMMEDIATE")
            validate_schema(conn)
        except BaseException:
            conn.close()
            raise
        self.connection = conn
        return self

    def ensure_active(self):
        if self.connection is None or self._finished or not self.connection.in_transaction:
            raise RuntimeError("repository requires an active UnitOfWork")
        if self.failed:
            raise RuntimeError("UnitOfWork has a failed write; rollback required")

    def commit(self):
        try:
            self.ensure_active()
            if list(self.connection.execute("PRAGMA foreign_key_check")):
                raise SchemaError("cross-character/missing foreign reference")
            for state in self.states.all():
                validate_aggregate(state, activities=self.activities.for_character(state.character_id),
                                   sessions=self.sessions.for_character(state.character_id),
                                   segments=self.segments.for_character(state.character_id),
                                   plans=self.plans.for_character(state.character_id))
                if any(event.state_version > state.state_version for event in self.events.for_character(state.character_id)):
                    raise SchemaError("event cannot refer to an uncommitted state version")
            self.connection.commit()
        except BaseException:
            if self.connection is not None and self.connection.in_transaction:
                self.connection.rollback()
            self._finished = True
            raise
        self._finished = True

    def __exit__(self, exc_type, exc, traceback):
        if self.connection is not None:
            if self.connection.in_transaction:
                self.connection.rollback()
            self.connection.close()
            self.connection = None
        self._finished = True
