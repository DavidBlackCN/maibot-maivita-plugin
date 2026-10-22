"""Explicit transactional schema migration; opening never upgrades a database."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import sqlite3

from ..domain.clock import Clock, to_epoch_ms


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str

    @property
    def checksum(self):
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def migrations() -> tuple[Migration, ...]:
    root = Path(__file__).with_name("migrations")
    return tuple(Migration(int(path.name.split('_', 1)[0]), path.read_text(encoding="utf-8"))
                 for path in sorted(root.glob("[0-9][0-9][0-9]_*.sql")))


def statements(script: str):
    """Unlike executescript, execute each statement inside the caller's BEGIN."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise SchemaError("incomplete migration statement")


def schema_signature(conn: sqlite3.Connection):
    return tuple(tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE substr(name,1,7) != 'sqlite_' ORDER BY type,name"))


@lru_cache(maxsize=8)
def expected_signature(steps: tuple[Migration, ...]):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for step in steps:
            for statement in statements(step.sql):
                conn.execute(statement)
        return schema_signature(conn)


def migration_version(conn: sqlite3.Connection, steps: tuple[Migration, ...]) -> int:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables:
        return 0
    if "schema_migrations" not in tables:
        raise SchemaError("unrecognized schema; legacy databases are not upgraded here")
    try:
        history = list(conn.execute("SELECT version,checksum FROM schema_migrations ORDER BY version"))
    except sqlite3.DatabaseError as exc:
        raise SchemaError("invalid migration ledger") from exc
    if not history:
        raise SchemaError("nonempty database has no migration history")
    if len(history) > len(steps) or history[-1][0] > steps[-1].version:
        raise SchemaError("database schema is newer than supported; no writes allowed")
    for row, step in zip(history, steps):
        if row[0] != step.version or row[1] != step.checksum:
            raise SchemaError("migration history/checksum mismatch")
    applied = steps[:len(history)]
    if schema_signature(conn) != expected_signature(applied):
        raise SchemaError("schema structure/index/trigger drift detected")
    return history[-1][0]


def validate_schema(conn: sqlite3.Connection, steps: tuple[Migration, ...] | None = None):
    steps = migrations() if steps is None else steps
    version = migration_version(conn, steps)
    if version != steps[-1].version:
        raise SchemaError("schema migration required; opening will not migrate implicitly")
    if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise SchemaError("database integrity check failed")
    if list(conn.execute("PRAGMA foreign_key_check")):
        raise SchemaError("database foreign key check failed")
    return version


def migrate(conn: sqlite3.Connection, clock: Clock, steps: tuple[Migration, ...] | None = None) -> int:
    steps = migrations() if steps is None else steps
    if not steps or [step.version for step in steps] != list(range(1, len(steps) + 1)):
        raise SchemaError("migrations must be contiguous and start at one")
    if conn.in_transaction:
        raise SchemaError("migration requires its own transaction")
    # SQLite's documented table-rebuild procedure requires FK enforcement off
    # before BEGIN. Full integrity/FK validation is mandatory before commit;
    # ordinary opens/UoWs always enforce FKs. No other connection can write.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN EXCLUSIVE")
    try:
        version = migration_version(conn, steps)
        for step in steps[version:]:
            for statement in statements(step.sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version,checksum,applied_at_ms) VALUES(?,?,?)",
                         (step.version, step.checksum, to_epoch_ms(clock.now_utc())))
        validate_schema(conn, steps)
        conn.commit()
        return steps[-1].version
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
