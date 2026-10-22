"""Immutable R1 records. Plans, observed facts and historical events are distinct.

These models validate data; they do not start activities, decide sleep, call an
LLM or authorize a command. R1-03 will own the command/commit protocol.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from datetime import date, datetime
from enum import StrEnum
import json
import math
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from .clock import as_utc


class DomainError(ValueError):
    pass


class SleepPhase(StrEnum):
    AWAKE = "awake"
    WINDING_DOWN = "winding_down"
    SLEEPING = "sleeping"
    TEMPORARILY_AWAKE = "temporarily_awake"
    WAKING = "waking"


class ActivityStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"


class ActivityRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"  # reserved; aggregate rejects secondary execution in R1


class Category(StrEnum):
    REST = "rest"
    STUDY = "study"
    WORK = "work"
    ENTERTAINMENT = "entertainment"
    DAILY_LIFE = "daily_life"
    SOCIAL = "social"
    TRAVEL = "travel"
    CREATIVE = "creative"
    OTHER = "other"


def category_for(value: str) -> Category:
    try:
        return Category(value)
    except ValueError:
        return Category.OTHER


class Visibility(StrEnum):
    PUBLIC_SUMMARY = "public_summary"
    SOURCE_STREAM = "source_stream"
    RESTRICTED = "restricted"


class PlanLevel(StrEnum):
    DIRECTION = "direction"
    DAILY = "daily"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class TimeMode(StrEnum):
    FIXED = "fixed"
    WINDOW = "window"
    UNSCHEDULED = "unscheduled"


class ItemStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    DONE = "done"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    CLOSED = "closed"


class SegmentKind(StrEnum):
    SLEEP = "sleep"
    AWAKE_INTERRUPT = "awake_interrupt"


class CommitmentStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    MISSED = "missed"
    NEEDS_REVIEW = "needs_review"


def require(condition: bool, message: str):
    if not condition:
        raise DomainError(message)


def number(value, minimum=0, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value), "finite number required")
    require(value >= minimum and (maximum is None or value <= maximum), "number outside bounds")


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, "nonnegative integer required")


def text(value):
    require(isinstance(value, str) and bool(value.strip()), "nonempty text required")


def sequence(value, *, identifiers=False):
    require(isinstance(value, (tuple, list)), "a sequence is required, not a scalar")
    if identifiers:
        for item in value:
            text(item)
    return tuple(value)


def freeze_json(value):
    if isinstance(value, Mapping):
        require(all(isinstance(k, str) for k in value), "JSON object keys must be strings")
        return MappingProxyType({k: freeze_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(v) for v in value)
    require(value is None or type(value) in (str, bool, int, float), "JSON value required")
    if isinstance(value, float):
        require(math.isfinite(value), "JSON number must be finite")
    return value


def plain_json(value):
    if isinstance(value, Mapping):
        return {k: plain_json(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [plain_json(v) for v in value]
    return value


def json_text(value) -> str:
    return json.dumps(plain_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def common(model, *, enums=None, ids=(), integers=()):
    for field in fields(model):
        value = getattr(model, field.name)
        if value is None and field.name.endswith("_at") and field.default is MISSING and field.default_factory is MISSING:
            raise DomainError("required observation instant is missing")
        if isinstance(value, datetime):
            object.__setattr__(model, field.name, as_utc(value))
        elif value is not None and (field.name.endswith("_at") or field.name == "interrupt_until"):
            raise DomainError("offset-aware datetime required")
        if value is not None and field.name.endswith("_id"):
            text(value)
    if hasattr(model, "character_id"):
        text(model.character_id)
    for name in ids:
        text(getattr(model, name))
    for name in integers:
        integer(getattr(model, name))
    for name, kind in (enums or {}).items():
        try:
            object.__setattr__(model, name, kind(getattr(model, name)))
        except (TypeError, ValueError) as exc:
            raise DomainError("unknown " + name) from exc


def interval(start, end, *, optional=False):
    if optional and start is None and end is None:
        return
    require(isinstance(start, datetime) and isinstance(end, datetime), "complete instant interval required")
    require(end > start, "interval must be nonempty and forward")


@dataclass(frozen=True, slots=True, kw_only=True)
class LifeState:
    updated_at: datetime
    last_advanced_at: datetime
    character_id: str = "default"
    state_version: int = 0
    plan_version: int = 0
    config_revision: int = 0
    sleep_phase: SleepPhase = SleepPhase.AWAKE
    primary_activity_id: str | None = None
    secondary_activity_ids: tuple[str, ...] = ()
    active_sleep_session_id: str | None = None
    active_plan_id: str | None = None
    last_wake_at: datetime | None = None
    awake_since_at: datetime | None = None
    energy: float | None = None
    fatigue: float | None = None
    stress: float | None = None
    sleep_debt_minutes: float | None = None
    sleep_history_coverage_days: int = 0
    provenance: str = "bootstrap_unknown"
    confidence: str = "unknown"
    degraded_reason: str | None = None

    def __post_init__(self):
        common(self, enums={"sleep_phase": SleepPhase}, integers=("state_version", "plan_version", "config_revision", "sleep_history_coverage_days"))
        text(self.provenance)
        require(self.last_advanced_at <= self.updated_at, "advance watermark cannot exceed observation")
        for value in (self.last_wake_at, self.awake_since_at):
            require(value is None or value <= self.updated_at, "future observation is invalid")
        for value in (self.energy, self.fatigue, self.stress):
            if value is not None:
                number(value, 0, 100)
        if self.sleep_debt_minutes is not None:
            number(self.sleep_debt_minutes)
            require(self.sleep_history_coverage_days > 0, "unknown history cannot imply known sleep debt")
        require(self.sleep_history_coverage_days <= 7, "sleep history coverage exceeds configured seven-day basis")
        secondary = sequence(self.secondary_activity_ids, identifiers=True)
        require(len(secondary) <= 2 and len(set(secondary)) == len(secondary), "invalid secondary references")
        require(self.primary_activity_id not in secondary, "primary cannot also be secondary")
        object.__setattr__(self, "secondary_activity_ids", secondary)
        sleeping = self.sleep_phase in (SleepPhase.SLEEPING, SleepPhase.TEMPORARILY_AWAKE)
        require(sleeping == (self.active_sleep_session_id is not None), "sleep phase/session mismatch")
        if self.sleep_phase == SleepPhase.SLEEPING:
            require(self.awake_since_at is None, "sleeping cannot have an awake start")
        if self.sleep_phase == SleepPhase.TEMPORARILY_AWAKE:
            require(self.awake_since_at is not None, "interruption requires an awake start")


@dataclass(frozen=True, slots=True, kw_only=True)
class Activity:
    activity_id: str
    character_id: str
    name: str
    started_at: datetime
    provenance: str
    category: Category = Category.OTHER
    role: ActivityRole = ActivityRole.PRIMARY
    description: str = ""
    ended_at: datetime | None = None
    status: ActivityStatus = ActivityStatus.ACTIVE
    source_schedule_item_id: str | None = None
    resumes_activity_id: str | None = None
    sleep_session_id: str | None = None
    interruption_reason: str | None = None
    end_reason: str | None = None
    visibility: Visibility = Visibility.RESTRICTED

    def __post_init__(self):
        common(self, enums={"status": ActivityStatus, "role": ActivityRole, "visibility": Visibility}, ids=("activity_id", "name", "provenance"))
        object.__setattr__(self, "category", category_for(self.category))
        require((self.status == ActivityStatus.ACTIVE) == (self.ended_at is None), "activity status/end mismatch")
        if self.ended_at is not None:
            interval(self.started_at, self.ended_at)
            text(self.end_reason)
        require(self.resumes_activity_id != self.activity_id, "activity cannot resume itself")

    def contains(self, instant: datetime) -> bool:
        instant = as_utc(instant)
        return self.started_at <= instant and (self.ended_at is None or instant < self.ended_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class Plan:
    plan_id: str
    character_id: str
    source: str
    level: PlanLevel = PlanLevel.DAILY
    version: int = 1
    status: PlanStatus = PlanStatus.DRAFT
    local_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    def __post_init__(self):
        common(self, enums={"level": PlanLevel, "status": PlanStatus}, ids=("plan_id", "source"), integers=("version",))
        require(self.version > 0, "plan version starts at one")
        require(self.local_date is None or type(self.local_date) is date, "civil date required")
        interval(self.start_at, self.end_at, optional=True)
        require(self.local_date is not None or self.start_at is not None, "plan needs a date or interval")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleItem:
    item_id: str
    character_id: str
    plan_id: str
    name: str
    source: str
    time_mode: TimeMode = TimeMode.UNSCHEDULED
    category: Category = Category.OTHER
    kind: str = "activity"
    status: ItemStatus = ItemStatus.PLANNED
    start_at: datetime | None = None
    end_at: datetime | None = None
    window_start_at: datetime | None = None
    window_end_at: datetime | None = None
    preferred_start_at: datetime | None = None
    preferred_duration_minutes: float | None = None
    priority: str = "medium"
    commitment_id: str | None = None
    visibility: Visibility = Visibility.RESTRICTED
    completion_evidence: str | None = None

    def __post_init__(self):
        common(self, enums={"time_mode": TimeMode, "status": ItemStatus, "visibility": Visibility}, ids=("item_id", "plan_id", "name", "source", "kind"))
        object.__setattr__(self, "category", category_for(self.category))
        require(self.priority in ("low", "medium", "high"), "invalid priority")
        if self.preferred_duration_minutes is not None:
            number(self.preferred_duration_minutes)
            require(self.preferred_duration_minutes > 0, "duration must be positive")
        if self.time_mode == TimeMode.FIXED:
            interval(self.start_at, self.end_at)
            require(all(v is None for v in (self.window_start_at, self.window_end_at, self.preferred_start_at, self.preferred_duration_minutes)), "fixed item contains window data")
        elif self.time_mode == TimeMode.WINDOW:
            require(self.start_at is None and self.end_at is None, "window item contains fixed times")
            interval(self.window_start_at, self.window_end_at)
            require(self.preferred_duration_minutes is not None, "window duration required")
            capacity = (self.window_end_at - self.window_start_at).total_seconds() / 60
            require(self.preferred_duration_minutes <= capacity, "duration exceeds window")
            if self.preferred_start_at is not None:
                require(self.window_start_at <= self.preferred_start_at < self.window_end_at, "preferred start outside window")
                require((self.window_end_at - self.preferred_start_at).total_seconds() / 60 >= self.preferred_duration_minutes, "preferred start leaves insufficient time")
        else:
            require(all(v is None for v in (self.start_at, self.end_at, self.window_start_at, self.window_end_at, self.preferred_start_at)), "unscheduled item contains precise times")
        if self.status == ItemStatus.DONE:
            text(self.completion_evidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleRevision:
    revision_id: str
    character_id: str
    plan_id: str
    basis_version: int
    result_version: int
    occurred_at: datetime
    reason_summary: str
    changes: tuple[Mapping[str, Any], ...] = ()
    decision_id: str | None = None
    trigger_event_id: str | None = None
    actor: str = "internal"

    def __post_init__(self):
        common(self, ids=("revision_id", "plan_id", "reason_summary", "actor"), integers=("basis_version", "result_version"))
        require(self.result_version == self.basis_version + 1, "revision must advance exactly one version")
        changes = sequence(self.changes)
        require(all(isinstance(change, Mapping) for change in changes), "revision changes must be objects")
        object.__setattr__(self, "changes", freeze_json(changes))


@dataclass(frozen=True, slots=True, kw_only=True)
class SleepSession:
    session_id: str
    character_id: str
    started_at: datetime
    expected_wake_at: datetime
    hard_deadline_at: datetime
    initial_expected_duration_minutes: float
    provenance: str
    kind: str = "night_sleep"
    status: SessionStatus = SessionStatus.ACTIVE
    ended_at: datetime | None = None
    end_reason: str | None = None
    decision_id: str | None = None
    extension_count: int = 0
    interrupt_until: datetime | None = None
    actual_sleep_minutes: float = 0
    recovery_marker: str | None = None
    revision: int = 1
    reason_summary: str = ""

    def __post_init__(self):
        common(self, enums={"status": SessionStatus}, ids=("session_id", "provenance"), integers=("extension_count", "revision"))
        require(self.kind in ("night_sleep", "nap"), "invalid sleep kind")
        require(self.revision > 0, "session revision starts at one")
        number(self.initial_expected_duration_minutes)
        require(self.initial_expected_duration_minutes > 0, "positive expected duration required")
        number(self.actual_sleep_minutes)
        require(self.started_at < self.expected_wake_at <= self.hard_deadline_at, "invalid sleep deadline ordering")
        require((self.status == SessionStatus.CLOSED) == (self.ended_at is not None), "session status/end mismatch")
        if self.ended_at is not None:
            interval(self.started_at, self.ended_at)
            require(self.ended_at <= self.hard_deadline_at, "closed session exceeds hard deadline")
            text(self.end_reason)
        if self.status == SessionStatus.INTERRUPTED:
            require(self.interrupt_until is not None and self.started_at < self.interrupt_until <= self.hard_deadline_at, "bounded interruption required")
        else:
            require(self.interrupt_until is None, "non-interrupted session has interruption deadline")
        # R2's autonomous duration/cooldown limits are deliberately not enforced
        # here: R1 legacy observations may use the old full fixed window.


@dataclass(frozen=True, slots=True, kw_only=True)
class SleepSegment:
    segment_id: str
    character_id: str
    session_id: str
    started_at: datetime
    provenance: str
    kind: SegmentKind = SegmentKind.SLEEP
    ended_at: datetime | None = None

    def __post_init__(self):
        common(self, enums={"kind": SegmentKind}, ids=("segment_id", "session_id", "provenance"))
        if self.ended_at is not None:
            interval(self.started_at, self.ended_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class Commitment:
    commitment_id: str
    character_id: str
    title: str
    source_stream_id: str
    provenance: str
    timezone: str = "Asia/Shanghai"
    local_date: date | None = None
    date_end: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: CommitmentStatus = CommitmentStatus.PROPOSED
    version: int = 1
    visibility: Visibility = Visibility.RESTRICTED
    participants: tuple[str, ...] = ()
    confirmation_basis: str | None = None
    fulfillment_evidence: str | None = None
    notes: str = ""

    def __post_init__(self):
        common(self, enums={"status": CommitmentStatus, "visibility": Visibility}, ids=("commitment_id", "title", "source_stream_id", "provenance"), integers=("version",))
        ZoneInfo(self.timezone)
        require(self.version > 0, "commitment version starts at one")
        require(self.local_date is None or type(self.local_date) is date, "civil date required")
        require(self.date_end is None or (type(self.date_end) is date and self.local_date is not None and self.date_end >= self.local_date), "invalid commitment date range")
        interval(self.start_at, self.end_at, optional=True)
        require(self.local_date is not None or self.start_at is not None, "commitment needs a date or interval")
        if self.status == CommitmentStatus.CONFIRMED:
            text(self.confirmation_basis)
        if self.status == CommitmentStatus.FULFILLED:
            text(self.fulfillment_evidence)
        object.__setattr__(self, "participants", sequence(self.participants, identifiers=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class LifeEvent:
    event_id: str
    character_id: str
    event_type: str
    state_version: int
    event_index: int
    occurred_at: datetime
    recorded_at: datetime
    payload: Mapping[str, Any]
    source: str
    schema_version: int = 1
    correlation_id: str | None = None
    causation_id: str | None = None
    visibility: Visibility = Visibility.PUBLIC_SUMMARY
    expires_at: datetime | None = None

    def __post_init__(self):
        common(self, enums={"visibility": Visibility}, ids=("event_id", "event_type", "source"), integers=("state_version", "event_index", "schema_version"))
        require(self.schema_version == 1, "unsupported event schema")
        require(self.event_type.startswith("life."), "life event type required")
        require(self.occurred_at <= self.recorded_at, "cannot record future facts")
        require(self.expires_at is None or self.expires_at >= self.occurred_at, "invalid event expiry")
        require(isinstance(self.payload, Mapping), "event payload must be an object")
        object.__setattr__(self, "payload", freeze_json(self.payload))


@dataclass(frozen=True, slots=True, kw_only=True)
class LifeGoal:
    goal_id: str
    character_id: str
    title: str
    provenance: str
    status: str = "active"
    stage: str = ""
    deadline_at: datetime | None = None
    progress_evidence: tuple[str, ...] = ()
    visibility: Visibility = Visibility.RESTRICTED

    def __post_init__(self):
        common(self, enums={"visibility": Visibility}, ids=("goal_id", "title", "provenance"))
        require(self.status in ("active", "paused", "completed", "cancelled"), "invalid goal status")
        object.__setattr__(self, "progress_evidence", sequence(self.progress_evidence, identifiers=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class Preference:
    preference_id: str
    character_id: str
    content: str
    source: str
    kind: str = "reference"
    allow_evolution: bool = False
    version: int = 1

    def __post_init__(self):
        common(self, ids=("preference_id", "content", "source"), integers=("version",))
        require(self.kind in ("reference", "hard_constraint"), "invalid preference kind")
        require(type(self.allow_evolution) is bool, "boolean evolution flag required")
        require(self.version > 0, "preference version starts at one")


@dataclass(frozen=True, slots=True, kw_only=True)
class Habit:
    habit_id: str
    character_id: str
    content: str
    evidence_ids: tuple[str, ...] = ()
    status: str = "candidate"
    confidence: float = 0
    weight: float = 0
    last_seen_at: datetime | None = None
    last_reviewed_at: datetime | None = None

    def __post_init__(self):
        common(self, ids=("habit_id", "content"))
        require(self.status in ("candidate", "active", "archived"), "invalid habit status")
        number(self.confidence, 0, 1)
        number(self.weight)
        object.__setattr__(self, "evidence_ids", sequence(self.evidence_ids, identifiers=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class LifeSummary:
    summary_id: str
    character_id: str
    start_at: datetime
    end_at: datetime
    content: str
    source_checksum: str
    source_ids: tuple[str, ...] = ()
    visibility: Visibility = Visibility.RESTRICTED

    def __post_init__(self):
        common(self, enums={"visibility": Visibility}, ids=("summary_id", "content", "source_checksum"))
        interval(self.start_at, self.end_at)
        object.__setattr__(self, "source_ids", sequence(self.source_ids, identifiers=True))
