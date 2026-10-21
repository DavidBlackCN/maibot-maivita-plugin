"""Pure data/transition guards, not a scheduler or autonomous sleep service."""
from __future__ import annotations

from collections.abc import Iterable
import math

from .models import (
    Activity, ActivityRole, ActivityStatus, Category, LifeState, Plan, PlanStatus,
    SessionStatus, SleepPhase, SleepSegment, SleepSession, require,
)


def validate_state_transition(before: LifeState, after: LifeState) -> None:
    require(before.character_id == after.character_id, "character identity cannot change")
    require(after.state_version == before.state_version + 1, "state transition must advance exactly one version")
    require(after.plan_version >= before.plan_version, "plan version cannot regress")
    require(after.config_revision >= before.config_revision, "config revision cannot regress")
    require(after.updated_at >= before.updated_at, "wall clock rollback needs explicit reconciliation")
    require(after.last_advanced_at >= before.last_advanced_at, "advance watermark cannot regress")
    if before.last_wake_at is not None:
        require(after.last_wake_at is not None and after.last_wake_at >= before.last_wake_at, "known full wake cannot regress or disappear")
    if before.active_sleep_session_id is not None and after.active_sleep_session_id is not None:
        require(after.last_wake_at == before.last_wake_at, "temporary interruption is not a new full wake")
    if before.active_sleep_session_id is not None and after.active_sleep_session_id is None:
        require(after.last_wake_at is not None, "closing a sleep session requires a full wake observation")
    allowed = {
        SleepPhase.AWAKE: {SleepPhase.AWAKE, SleepPhase.WINDING_DOWN, SleepPhase.SLEEPING},
        SleepPhase.WINDING_DOWN: {SleepPhase.WINDING_DOWN, SleepPhase.AWAKE, SleepPhase.SLEEPING},
        # A deterministic waking -> awake collapse may occur in one commit.
        SleepPhase.SLEEPING: {SleepPhase.SLEEPING, SleepPhase.TEMPORARILY_AWAKE, SleepPhase.WAKING, SleepPhase.AWAKE},
        SleepPhase.TEMPORARILY_AWAKE: {SleepPhase.TEMPORARILY_AWAKE, SleepPhase.SLEEPING, SleepPhase.WAKING, SleepPhase.AWAKE},
        SleepPhase.WAKING: {SleepPhase.WAKING, SleepPhase.AWAKE},
    }
    require(after.sleep_phase in allowed[before.sleep_phase], "illegal sleep phase transition")
    if before.active_sleep_session_id is not None and after.active_sleep_session_id is not None:
        require(before.active_sleep_session_id == after.active_sleep_session_id, "interruption must reuse the same session")


def validate_activity_transition(before: Activity, after: Activity) -> None:
    require(before.status == ActivityStatus.ACTIVE, "closed activity is immutable")
    for name in ("activity_id", "character_id", "started_at", "name", "description", "category", "role",
                 "provenance", "source_schedule_item_id", "resumes_activity_id", "sleep_session_id", "visibility"):
        require(getattr(before, name) == getattr(after, name), "activity facts cannot be rewritten")
    require(after.status != ActivityStatus.ACTIVE, "activity update must close the observed segment")


def validate_aggregate(
    state: LifeState, *, activities: Iterable[Activity] = (), sessions: Iterable[SleepSession] = (),
    segments: Iterable[SleepSegment] = (), plans: Iterable[Plan] = (),
) -> None:
    """Check snapshot and actual records at a transaction boundary.

    Merely adding a Plan/ScheduleItem cannot produce any observed activity or
    sleep session. Historical closed records remain separately identifiable.
    """
    activities, sessions, segments, plans = map(tuple, (activities, sessions, segments, plans))
    for records in (activities, sessions, segments, plans):
        require(all(r.character_id == state.character_id for r in records), "cross-character reference")
    secondary = [a for a in activities if a.role == ActivityRole.SECONDARY and a.status == ActivityStatus.ACTIVE]
    require(set(state.secondary_activity_ids) == {a.activity_id for a in secondary} and len(secondary) <= 2, "secondary snapshot/limit mismatch")
    require(not secondary or (state.sleep_phase in ('awake','winding_down') and state.primary_activity_id is not None), "secondary requires awake primary")
    active = [a for a in activities if a.status == ActivityStatus.ACTIVE and a.role == ActivityRole.PRIMARY]
    require(len(active) <= 1, "at most one active primary activity")
    require(state.primary_activity_id == (active[0].activity_id if active else None), "primary snapshot/activity mismatch")
    require(all(a.started_at <= state.updated_at and (a.ended_at is None or a.ended_at <= state.updated_at) for a in activities), "activity is not an observed fact yet")
    ordered = sorted((a for a in activities if a.role == ActivityRole.PRIMARY), key=lambda a: a.started_at)
    by_id = {a.activity_id: a for a in activities}
    compatible={('daily_life','entertainment'),('entertainment','daily_life'),('rest','entertainment')}
    boundaries=[]
    for actual in activities:
        if actual.role != ActivityRole.SECONDARY: continue
        require(actual.sleep_session_id is None, 'secondary cannot belong to sleep')
        container=next((p for p in ordered if p.started_at<=actual.started_at
            and (p.ended_at is None or (actual.ended_at is not None and actual.ended_at<=p.ended_at))),None)
        require(container is not None and container.sleep_session_id is None
            and (container.category,actual.category) in compatible, 'secondary requires compatible covering primary')
        boundaries.append((actual.started_at,1))
        if actual.ended_at is not None: boundaries.append((actual.ended_at,-1))
    count=0
    for _,change in sorted(boundaries):
        count+=change
        require(count<=2, 'secondary history exceeds two concurrent activities')
    for index, actual in enumerate(ordered):
        if index:
            previous = ordered[index - 1]
            require(previous.ended_at is not None and previous.ended_at <= actual.started_at, "actual primary history overlaps")
        if actual.resumes_activity_id is not None:
            previous = by_id.get(actual.resumes_activity_id)
            require(previous is not None and previous.ended_at is not None and previous.ended_at <= actual.started_at, "invalid resumed activity history")
    open_sessions = [s for s in sessions if s.status != SessionStatus.CLOSED]
    require(len(open_sessions) <= 1, "at most one open sleep session")
    require(state.active_sleep_session_id == (open_sessions[0].session_id if open_sessions else None), "snapshot/session mismatch")
    if state.active_plan_id is not None:
        plan = next((p for p in plans if p.plan_id == state.active_plan_id), None)
        require(plan is not None and plan.status == PlanStatus.ACTIVE and plan.version == state.plan_version, "active plan/version mismatch")
    session_by_id = {s.session_id: s for s in sessions}
    require(len(session_by_id) == len(sessions), "duplicate session ID")
    ordered_sessions = sorted(sessions, key=lambda s: s.started_at)
    for index, session in enumerate(ordered_sessions):
        if index:
            previous = ordered_sessions[index - 1]
            require(previous.ended_at is not None and previous.ended_at <= session.started_at, "sleep session history overlaps")
    for actual in activities:
        if actual.sleep_session_id is not None:
            session = session_by_id.get(actual.sleep_session_id)
            require(session is not None and session.started_at <= actual.started_at, "activity starts outside its sleep session")
            require(actual.ended_at is None or actual.ended_at <= (session.ended_at or state.updated_at), "activity ends outside its sleep session")
    require(all(s.session_id in session_by_id for s in segments), "segment without session")
    for session in sessions:
        require(session.started_at <= state.updated_at, "future session cannot be observed")
        require(session.ended_at is None or session.ended_at <= state.updated_at, "future session end cannot be observed")
        own = sorted((s for s in segments if s.session_id == session.session_id), key=lambda s: s.started_at)
        opened = [s for s in own if s.ended_at is None]
        require(len(opened) <= 1, "at most one open segment per session")
        for index, segment in enumerate(own):
            require(session.started_at <= segment.started_at <= state.updated_at, "segment start outside observations")
            if segment.ended_at is not None:
                require(segment.ended_at <= (session.ended_at or state.updated_at), "segment end outside session")
            if index:
                require(own[index - 1].ended_at is not None and own[index - 1].ended_at <= segment.started_at, "sleep segments overlap")
        total = sum((s.ended_at - s.started_at).total_seconds() / 60 for s in own
                    if s.ended_at is not None and s.kind == "sleep")
        require(math.isclose(total, session.actual_sleep_minutes, abs_tol=1 / 60000), "sleep duration cache disagrees with closed segments")
        if session.status == SessionStatus.CLOSED:
            require(not opened, "closed session has open segment")
        else:
            require(len(opened) == 1, "open session requires one observed segment")
            expected_kind = "sleep" if session.status == SessionStatus.ACTIVE else "awake_interrupt"
            expected_phase = SleepPhase.SLEEPING if session.status == SessionStatus.ACTIVE else SleepPhase.TEMPORARILY_AWAKE
            require(state.sleep_phase == expected_phase and opened[0].kind == expected_kind, "phase/session/segment mismatch")
            require(bool(active) and active[0].sleep_session_id == session.session_id, "sleep session requires matching primary activity")
            require(active[0].started_at == opened[0].started_at, "activity/segment starts differ")
            if session.status == SessionStatus.ACTIVE:
                require(active[0].category == Category.REST, "sleep requires a rest activity")
            else:
                require(state.awake_since_at == opened[0].started_at, "awake interruption start mismatch")
