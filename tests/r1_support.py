from dataclasses import replace
from datetime import datetime, timedelta

from r0_support import ROOT
from maibot_autonomous_planning_plugin.domain.clock import ManualClock, UTC
from maibot_autonomous_planning_plugin.domain.models import Activity, LifeState, SleepSegment, SleepSession

AT = datetime(2026, 10, 21, 12, tzinfo=UTC)


def clock():
    return ManualClock(AT)


def state(**changes):
    return replace(LifeState(updated_at=AT, last_advanced_at=AT), **changes)


def activity(**changes):
    return replace(Activity(activity_id="a1", character_id="default", name="Observed study",
                            started_at=AT - timedelta(minutes=30), category="study", provenance="test_observed"), **changes)


def sleeping():
    start = AT - timedelta(minutes=30)
    session = SleepSession(session_id="s1", character_id="default", started_at=start,
                           expected_wake_at=AT + timedelta(hours=8), hard_deadline_at=AT + timedelta(hours=12),
                           initial_expected_duration_minutes=510, provenance="legacy_observed")
    segment = SleepSegment(segment_id="seg1", character_id="default", session_id="s1", started_at=start,
                           provenance="legacy_observed")
    actual = activity(name="Observed rest", category="rest", sleep_session_id="s1")
    snapshot = state(sleep_phase="sleeping", primary_activity_id="a1", active_sleep_session_id="s1")
    return snapshot, actual, session, segment
