"""Typed repositories attached to a UnitOfWork; no independent commits."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from functools import wraps
import json

from ..domain.clock import from_epoch_ms, to_epoch_ms
from ..domain.constraints import validate_activity_transition, validate_state_transition
from ..domain.models import (
    Activity, Commitment, LifeEvent, LifeGoal, LifeState, Plan, Preference,
    ScheduleItem, ScheduleRevision, SleepSegment, SleepSession, Habit, LifeSummary, json_text,
)


class RepositoryError(RuntimeError):
    pass


def write_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.uow.failed = True
            raise
    return wrapped


@dataclass(frozen=True)
class RecordSpec:
    table: str
    model: type
    key: str
    json_fields: tuple[str, ...] = ()
    date_fields: tuple[str, ...] = ()
    bool_fields: tuple[str, ...] = ()
    immutable: bool = False
    # Domain units remain unchanged; only SQLite stores scaled integers.
    fixed_fields: tuple[tuple[str, int], ...] = ()
    positive_minutes: tuple[str, ...] = ()

    def column(self, name):
        if name in self.json_fields:
            return name + "_json"
        if name.endswith("_at") or name == "interrupt_until":
            return name + "_ms"
        return name

    @property
    def names(self):
        return tuple(field.name for field in fields(self.model))

    @property
    def columns(self):
        return tuple(self.column(name) for name in self.names)

    def encode(self, model):
        if type(model) is not self.model:
            raise TypeError("repository received the wrong domain model")
        values = []
        scales = dict(self.fixed_fields)
        for name in self.names:
            value = getattr(model, name)
            if name in scales and value is not None:
                # Match SQLite ROUND in migration 006 (ties away from zero),
                # avoiding binary-float multiplication and banker's rounding.
                value = int((Decimal(str(value)) * scales[name]).to_integral_value(rounding=ROUND_HALF_UP))
                if name in self.positive_minutes:
                    value = max(1, value)
            elif name in self.json_fields:
                value = json_text(value)
            elif isinstance(value, datetime):
                value = to_epoch_ms(value)
            elif type(value) is date:
                value = value.isoformat()
            elif isinstance(value, StrEnum):
                value = value.value
            elif name in self.bool_fields:
                value = int(value)
            values.append(value)
        return values

    def decode(self, row):
        data = {}
        scales = dict(self.fixed_fields)
        for name, column in zip(self.names, self.columns):
            value = row[column]
            if value is not None:
                if name in scales:
                    value = value if scales[name] == 1 else value / scales[name]
                elif name in self.json_fields:
                    value = json.loads(value)
                elif name in self.date_fields:
                    value = date.fromisoformat(value)
                elif name.endswith("_at") or name == "interrupt_until":
                    value = from_epoch_ms(value)
                elif name in self.bool_fields:
                    value = bool(value)
            data[name] = value
        return self.model(**data)


SPECS = {
    "states": RecordSpec("life_state", LifeState, "character_id", ("secondary_activity_ids",),
                         fixed_fields=(("energy", 100), ("fatigue", 100), ("stress", 100), ("sleep_debt_minutes", 1))),
    "activities": RecordSpec("activities", Activity, "activity_id", fixed_fields=(("energy_rate", 1000),)),
    "plans": RecordSpec("plans", Plan, "plan_id", date_fields=("local_date",)),
    "items": RecordSpec("schedule_items", ScheduleItem, "item_id", fixed_fields=(("preferred_duration_minutes", 1),),
                        positive_minutes=("preferred_duration_minutes",)),
    "sessions": RecordSpec("sleep_sessions", SleepSession, "session_id",
                           fixed_fields=(("initial_expected_duration_minutes", 1), ("actual_sleep_minutes", 1)),
                           positive_minutes=("initial_expected_duration_minutes",)),
    "segments": RecordSpec("sleep_segments", SleepSegment, "segment_id"),
    "commitments": RecordSpec("commitments", Commitment, "commitment_id", ("participants",), ("local_date", "date_end")),
    "events": RecordSpec("life_events", LifeEvent, "event_id", ("payload",), immutable=True),
    "revisions": RecordSpec("schedule_revisions", ScheduleRevision, "revision_id", ("changes",), immutable=True),
    "goals": RecordSpec("life_goals", LifeGoal, "goal_id", ("progress_evidence",)),
    "preferences": RecordSpec("life_preferences", Preference, "preference_id", bool_fields=("allow_evolution",)),
    "habits": RecordSpec("habits", Habit, "habit_id", ("evidence_ids",)),
    "summaries": RecordSpec("life_summaries", LifeSummary, "summary_id", ("source_ids",), immutable=True),
}


class Repository:
    def __init__(self, uow, spec: RecordSpec):
        self.uow = uow
        self.spec = spec

    @property
    def conn(self):
        self.uow.ensure_active()
        return self.uow.connection

    @write_operation
    def add(self, model):
        values = self.spec.encode(model)
        self.conn.execute(f"INSERT INTO {self.spec.table} ({','.join(self.spec.columns)}) VALUES ({','.join('?' for _ in values)})", values)

    def get(self, key):
        row = self.conn.execute(f"SELECT {','.join(self.spec.columns)} FROM {self.spec.table} WHERE {self.spec.key}=?", (key,)).fetchone()
        return self.spec.decode(row) if row is not None else None

    def all(self):
        return [self.spec.decode(row) for row in self.conn.execute(f"SELECT {','.join(self.spec.columns)} FROM {self.spec.table} ORDER BY {self.spec.key}")]

    def for_character(self, character_id):
        return [self.spec.decode(row) for row in self.conn.execute(
            f"SELECT {','.join(self.spec.columns)} FROM {self.spec.table} WHERE character_id=? ORDER BY {self.spec.key}", (character_id,))]

    @write_operation
    def update(self, model, *, expected_version: int | None = None):
        if self.spec.immutable:
            raise RepositoryError("historical event/revision is append-only")
        old = self.get(getattr(model, self.spec.key))
        if old is None:
            raise RepositoryError("record does not exist")
        encoded = self.spec.encode(model)
        # Compare persisted precision, including when the caller closes the
        # original Activity instance whose rate has since been quantized.
        persisted = self.spec.decode(dict(zip(self.spec.columns, encoded)))
        if isinstance(model, LifeState):
            if expected_version is None or old.state_version != expected_version:
                raise RepositoryError("stale state version")
            validate_state_transition(old, persisted)
        elif isinstance(model, Activity):
            validate_activity_transition(old, persisted)
        # Do not rewrite unchanged observed fields. Fixed-point encoding handles
        # SQLite 3.37 compatibility even when body readings actually change.
        changes = [(column, value) for name, column, value in zip(self.spec.names, self.spec.columns, encoded)
                   if getattr(persisted, name) != getattr(old, name)]
        if not changes:
            return
        values = [value for column, value in changes]
        assignments = ",".join(column + "=?" for column, value in changes)
        sql = f"UPDATE {self.spec.table} SET {assignments} WHERE {self.spec.key}=?"
        values.append(getattr(model, self.spec.key))
        if isinstance(model, LifeState):
            sql += " AND state_version=?"
            values.append(expected_version)
        if self.conn.execute(sql, values).rowcount != 1:
            raise RepositoryError("concurrent record update")
