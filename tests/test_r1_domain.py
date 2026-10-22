from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from pathlib import Path
import subprocess
import sys
import unittest

from r1_support import ROOT, AT, activity, clock, sleeping, state
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import (
    AmbiguousLocalTime, NonexistentLocalTime, SystemClock, UTC, as_utc,
    from_epoch_ms, local_day_bounds, resolve_local, to_epoch_ms, to_local,
)
from maibot_autonomous_planning_plugin.maivita.life.domain.constraints import (
    validate_activity_transition, validate_aggregate, validate_state_transition,
)
from maibot_autonomous_planning_plugin.maivita.life.domain.models import (
    Activity, Category, Commitment, DomainError, Habit, LifeEvent, LifeGoal, LifeState, LifeSummary,
    Plan, Preference, ScheduleItem, ScheduleRevision, SleepSegment, SleepSession, json_text,
)


class ClockTests(unittest.TestCase):
    def test_T09_epoch_roundtrip_precision_offsets_and_negative_instants(self):
        for instant in [AT, AT.replace(microsecond=123999), datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)]:
            self.assertEqual(from_epoch_ms(to_epoch_ms(instant)), as_utc(instant))
        local = datetime.fromisoformat("2026-10-21T20:00:00+08:00")
        self.assertEqual(to_epoch_ms(local), to_epoch_ms(AT))
        self.assertEqual(to_local(AT, "Asia/Shanghai").hour, 20)
        for invalid in [True, 1.5, "1"]:
            with self.assertRaises(ValueError):
                from_epoch_ms(invalid)
        with self.assertRaises(ValueError):
            to_epoch_ms(datetime(2026, 10, 21))

    def test_T09_DST_day_lengths_and_ambiguous_nonexistent_local_times(self):
        for day, hours in [(date(2024, 3, 10), 23), (date(2024, 11, 3), 25)]:
            start, end = local_day_bounds(day, "America/New_York")
            self.assertEqual(end - start, timedelta(hours=hours))
        local = datetime(2024, 11, 3, 1, 30)
        with self.assertRaises(AmbiguousLocalTime):
            resolve_local(local, "America/New_York")
        self.assertEqual(resolve_local(local, "America/New_York", fold=1) -
                         resolve_local(local, "America/New_York", fold=0), timedelta(hours=1))
        with self.assertRaises(NonexistentLocalTime):
            resolve_local(datetime(2024, 3, 10, 2, 30), "America/New_York")
        with self.assertRaises(NonexistentLocalTime):
            local_day_bounds(date(2011, 12, 30), "Pacific/Apia")

    def test_T09_cross_midnight_civil_day_bounds_do_not_use_machine_timezone(self):
        start, end = local_day_bounds(date(2026, 10, 22), "Asia/Shanghai")
        self.assertEqual(start, datetime(2026, 10, 21, 16, tzinfo=UTC))
        self.assertEqual(end, start + timedelta(days=1))
        with self.assertRaises(ValueError):
            resolve_local(AT, "Asia/Shanghai")

    def test_T09_clock_wall_rollback_does_not_reverse_elapsed_time(self):
        fake = clock()
        fake.advance(timedelta(seconds=3))
        fake.set_utc(AT - timedelta(days=1))
        self.assertEqual(fake.monotonic(), 3)
        with self.assertRaises(ValueError):
            fake.advance(timedelta(seconds=-1))
        real = SystemClock()
        self.assertEqual(real.now_utc().utcoffset(), timedelta(0))
        self.assertGreaterEqual(real.monotonic(), 0)


class DomainTests(unittest.TestCase):
    def test_core_imports_require_no_SDK_and_have_no_database_side_effect(self):
        script = (
            "import sys,types; from pathlib import Path; "
            "p=types.ModuleType('standalone'); p.__path__=[sys.argv[1]]; sys.modules['standalone']=p; "
            "sys.modules['maibot_sdk']=None; from standalone.maivita.life.domain import models,clock,constraints; "
            "assert models.Category.OTHER == 'other'"
        )
        subprocess.run([sys.executable, "-I", "-c", script, str(ROOT)], check=True, capture_output=True)

    def test_unknown_bootstrap_does_not_invent_wake_sleep_or_energy_history(self):
        snapshot = state()
        validate_aggregate(snapshot)
        self.assertIsNone(snapshot.primary_activity_id)
        self.assertIsNone(snapshot.last_wake_at)
        self.assertIsNone(snapshot.awake_since_at)
        self.assertIsNone(snapshot.sleep_debt_minutes)
        self.assertEqual((snapshot.energy, snapshot.fatigue, snapshot.stress), (None, None, None))

    def test_T06_state_and_number_constraints_reject_bad_or_inconsistent_values(self):
        for values in [{"energy": float("nan")}, {"fatigue": float("inf")}, {"energy": -1},
                       {"stress": 101}, {"state_version": True}, {"sleep_debt_minutes": 0},
                       {"sleep_phase": "sleeping"}, {"active_sleep_session_id": "s1"},
                       {"updated_at": None}, {"last_wake_at": AT + timedelta(seconds=1)}]:
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                state(**values)

    def test_T06_single_primary_and_session_guards(self):
        snap, actual, session, segment = sleeping()
        validate_aggregate(snap, activities=[actual], sessions=[session], segments=[segment])
        with self.assertRaises(DomainError):
            validate_aggregate(snap, activities=[actual, replace(actual, activity_id="a2")], sessions=[session], segments=[segment])
        with self.assertRaises(DomainError):
            validate_aggregate(snap, activities=[actual], sessions=[session, replace(session, session_id="s2")], segments=[segment])
        with self.assertRaises(DomainError):
            validate_aggregate(state(), activities=[activity()])
        with self.assertRaises(DomainError):
            validate_aggregate(state(character_id="other"), activities=[activity()])

    def test_plan_sleep_item_is_not_observed_sleep_or_a_completed_activity(self):
        plan = Plan(plan_id="p", character_id="default", source="test", local_date=date(2026, 10, 21))
        item = ScheduleItem(item_id="sleep-plan", plan_id="p", character_id="default", name="Sleep intention",
                            source="test", time_mode="fixed", start_at=AT, end_at=AT + timedelta(hours=8))
        fake = clock()
        fake.advance(timedelta(days=1))
        snapshot = state()
        validate_aggregate(snapshot, plans=[plan])
        self.assertEqual(snapshot.sleep_phase, "awake")
        self.assertEqual(item.status, "planned")
        self.assertIsNone(snapshot.active_sleep_session_id)
        with self.assertRaises(DomainError):
            replace(item, status="done")

    def test_clearing_active_plan_preserves_the_monotonic_historical_version(self):
        before = state(state_version=4, plan_version=3, active_plan_id="old-plan")
        after = replace(before, state_version=5, active_plan_id=None)
        validate_state_transition(before, after)
        validate_aggregate(after)
        self.assertEqual(after.plan_version, 3)

    def test_time_modes_are_validated_without_generating_a_hybrid_plan(self):
        base = ScheduleItem(item_id="i", plan_id="p", character_id="default", name="Maybe study", source="test")
        self.assertIsNone(base.start_at)
        window = replace(base, time_mode="window", window_start_at=AT, window_end_at=AT + timedelta(hours=1),
                         preferred_duration_minutes=30, preferred_start_at=AT + timedelta(minutes=30))
        self.assertIsNone(window.start_at)
        for changes in [{"start_at": AT}, {"time_mode": "fixed", "start_at": AT, "end_at": AT},
                        {"time_mode": "window", "window_start_at": AT, "window_end_at": AT + timedelta(minutes=10), "preferred_duration_minutes": 20}]:
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                replace(base, **changes)

    def test_T09_actual_activity_intervals_are_half_open(self):
        old = activity(ended_at=AT, status="completed", end_reason="explicit_completion")
        new = activity(activity_id="next", started_at=AT)
        self.assertFalse(old.contains(AT))
        self.assertTrue(new.contains(AT))
        validate_activity_transition(activity(), old)
        with self.assertRaises(DomainError):
            validate_activity_transition(old, replace(old, description="rewrite history"))
        with self.assertRaises(DomainError):
            replace(old, ended_at=old.started_at)

    def test_transition_guards_check_versions_clock_and_session_identity(self):
        before = state()
        validate_state_transition(before, replace(before, state_version=1))
        for after in [replace(before, state_version=2), replace(before, character_id="other", state_version=1),
                      replace(before, state_version=1, updated_at=AT - timedelta(days=1), last_advanced_at=AT - timedelta(days=1)),
                      replace(before, state_version=1, sleep_phase="waking")]:
            with self.assertRaises(DomainError):
                validate_state_transition(before, after)
        asleep, _, _, _ = sleeping()
        with self.assertRaises(DomainError):
            validate_state_transition(asleep, replace(asleep, state_version=1, active_sleep_session_id="different-session"))

    def test_interruption_segments_exclude_awake_time_and_do_not_reset_full_wake(self):
        snap, actual, session, segment = sleeping()
        interrupted = replace(session, status="interrupted", interrupt_until=AT + timedelta(minutes=10), actual_sleep_minutes=30)
        closed = replace(segment, ended_at=AT)
        awake = replace(segment, segment_id="interrupt", started_at=AT, kind="awake_interrupt")
        old_wake = AT - timedelta(days=1)
        after = state(sleep_phase="temporarily_awake", primary_activity_id="awake", active_sleep_session_id="s1",
                      awake_since_at=AT, last_wake_at=old_wake)
        current = replace(actual, activity_id="awake", name="Awake interruption", started_at=AT)
        validate_aggregate(after, activities=[current], sessions=[interrupted], segments=[closed, awake])
        self.assertEqual(after.last_wake_at, old_wake)
        with self.assertRaises(DomainError):
            validate_aggregate(after, activities=[current], sessions=[replace(interrupted, actual_sleep_minutes=40)], segments=[closed, awake])

    def test_sleep_models_preserve_legacy_window_without_implementing_R2_limits(self):
        _, _, session, _ = sleeping()
        legacy = replace(session, expected_wake_at=session.started_at + timedelta(hours=24),
                         hard_deadline_at=session.started_at + timedelta(hours=24), initial_expected_duration_minutes=1440)
        self.assertEqual(legacy.initial_expected_duration_minutes, 1440)
        with self.assertRaises(DomainError):
            replace(session, expected_wake_at=session.started_at)

    def test_full_wake_transition_can_collapse_waking_but_interruption_is_not_full_wake(self):
        before, _, _, _ = sleeping()
        after = state(state_version=1, last_wake_at=AT, awake_since_at=AT)
        validate_state_transition(before, after)
        with self.assertRaises(DomainError):
            validate_state_transition(before, replace(after, last_wake_at=None))
        interrupted = replace(before, state_version=1, sleep_phase="temporarily_awake", awake_since_at=AT, last_wake_at=AT)
        with self.assertRaises(DomainError):
            validate_state_transition(before, interrupted)

    def test_closed_actual_history_cannot_overlap_and_resume_requires_closed_predecessor(self):
        closed = activity(ended_at=AT, status="interrupted", end_reason="fixture interruption")
        resumed = activity(activity_id="resumed", started_at=AT, resumes_activity_id="a1")
        validate_aggregate(state(primary_activity_id="resumed"), activities=[closed, resumed])
        with self.assertRaises(DomainError):
            validate_aggregate(state(primary_activity_id="resumed"), activities=[closed, replace(resumed, started_at=AT - timedelta(minutes=1))])

    def test_date_only_commitment_keeps_unknown_time_and_requires_fulfillment_evidence(self):
        promise = Commitment(commitment_id="c", character_id="default", title="Future promise",
                             source_stream_id="private-source", provenance="test", local_date=date(2099, 1, 1), status="needs_review")
        self.assertIsNone(promise.start_at)
        self.assertEqual(promise.visibility, "restricted")
        with self.assertRaises(DomainError):
            replace(promise, status="fulfilled")
        self.assertEqual(replace(promise, status="confirmed", confirmation_basis="fixture confirmation").status, "confirmed")

    def test_event_and_revision_payloads_are_deeply_immutable(self):
        payload = {"details": [1, {"safe": True}]}
        event = LifeEvent(event_id="e", character_id="default", event_type="life.activity.started", state_version=0,
                          event_index=0, occurred_at=AT, recorded_at=AT, payload=payload, source="test")
        payload["details"].append("caller mutation")
        self.assertNotIn("caller mutation", json_text(event.payload))
        with self.assertRaises(TypeError):
            event.payload["details"][1]["safe"] = False
        with self.assertRaises(FrozenInstanceError):
            event.event_type = "life.forged"
        with self.assertRaises(DomainError):
            replace(event, payload={"bad": float("nan")})
        revision = ScheduleRevision(revision_id="r", character_id="default", plan_id="p", basis_version=1,
                                    result_version=2, occurred_at=AT, reason_summary="fixture", changes=({"op": "add"},))
        with self.assertRaises(TypeError):
            revision.changes[0]["op"] = "delete"

    def test_R1_reserved_continuity_records_do_not_start_learning_or_cleanup(self):
        goal = LifeGoal(goal_id="g", character_id="default", title="Long-term", provenance="test")
        preference = Preference(preference_id="pref", character_id="default", content="Reference", source="test")
        habit = Habit(habit_id="h", character_id="default", content="Unconfirmed")
        summary = LifeSummary(summary_id="sum", character_id="default", start_at=AT - timedelta(days=1),
                              end_at=AT, content="Fixture only", source_checksum="synthetic")
        self.assertEqual(goal.progress_evidence, ())
        self.assertFalse(preference.allow_evolution)
        self.assertEqual(habit.status, "candidate")
        self.assertEqual(summary.source_ids, ())

    def test_identifier_sequences_do_not_silently_split_scalar_strings(self):
        with self.assertRaises(DomainError):
            state(secondary_activity_ids="ab")
        with self.assertRaises(DomainError):
            Commitment(commitment_id="c", character_id="default", title="fixture", source_stream_id="s",
                       provenance="test", local_date=date(2099, 1, 1), participants="private-user")
        with self.assertRaises(DomainError):
            LifeGoal(goal_id="g", character_id="default", title="fixture", provenance="test", progress_evidence="not-an-array")
