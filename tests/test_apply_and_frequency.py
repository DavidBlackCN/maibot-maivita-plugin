import asyncio
import unittest
from pathlib import Path
import sys
import types
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

from maibot_autonomous_planning_plugin.maivita.legacy.core.models import Schedule, ScheduleItem, ScheduleType
from maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_generator import ScheduleGenerator
from maibot_autonomous_planning_plugin.maivita.legacy.planner.goal_manager import GoalManager
from maibot_autonomous_planning_plugin.maivita.legacy.services.proactive_service import ProactiveService
from maibot_autonomous_planning_plugin.maivita.legacy.core.frequency import calculate_frequency
from maibot_autonomous_planning_plugin.maivita.legacy.config_models import ScheduleFrequencyConfig


class FakeGoalManager:
    def __init__(self):
        self.batch_calls = 0

    def create_goals_batch(self, data):
        self.batch_calls += 1
        return []

    def get_pending_commitments(self, _date):
        return []


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_rejects_entire_invalid_batch(self):
        manager = FakeGoalManager()
        generator = ScheduleGenerator(manager, config={"no_sleep_mode": True})
        schedule = Schedule(ScheduleType.DAILY, "invalid", [
            ScheduleItem("A", "", "custom", "medium", "09:00", 2),
            ScheduleItem("B", "", "custom", "medium", "10:00", 1),
        ])
        with self.assertRaisesRegex(Exception, "最终校验失败"):
            await generator.apply_schedule(schedule, "user", "global")
        self.assertEqual(manager.batch_calls, 0)


class BatchRollbackTests(unittest.TestCase):
    def test_mid_batch_failure_compensates_committed_rows(self):
        deleted = []

        class Manager(GoalManager):
            def __init__(self):
                self.calls = 0
                self.db = type("DB", (), {"delete_goal": lambda _self, goal_id: deleted.append(goal_id)})()

            def create_goal(self, **data):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("db failure")
                return type("Goal", (), {"goal_id": "first"})()

        payload = {
            "name": "x", "description": "x", "goal_type": "custom",
            "creator_id": "u", "chat_id": "c", "priority": "medium",
        }
        with self.assertRaisesRegex(RuntimeError, "db failure"):
            Manager().create_goals_batch([dict(payload), dict(payload)])
        self.assertEqual(deleted, ["first"])


class FakeFrequency:
    def __init__(self):
        self.calls = []

    async def set_adjust(self, stream_id, value):
        self.calls.append((stream_id, value))
        return True


    async def remove_adjust(self, stream_id):
        self.calls.append((stream_id, 1.0))
        return True


class FakeChat:
    async def get_all_streams(self):
        return [{"session_id": "one"}, {"session_id": "two"}]


class FrequencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_sleep_then_awake_uses_adjust_only(self):
        plugin = type("Plugin", (), {})()
        plugin.ctx = type("Ctx", (), {"frequency": FakeFrequency()})()
        service = ProactiveService.__new__(ProactiveService)
        service._plugin = plugin
        plugin._frequency = plugin.ctx.frequency
        service._current_factor = {}
        await service._set_adjust(["chat"], 0.0, "sleeping")
        await service._set_adjust(["chat"], 1.0, "stopped")
        self.assertEqual(plugin.ctx.frequency.calls, [("chat", 0.0), ("chat", 1.0)])

    async def test_independent_sleep_and_activity_switches(self):
        factors = SimpleNamespace(study=0.3, entertainment=1.5)
        for sleep_enabled, activity_enabled, sleeping, goal_type, expected in [
            (False, False, False, "study", 1.0),
            (True, False, True, "study", 0.0),
            (True, True, False, "entertainment", 1.5),
            (False, True, False, "study", 0.3),
        ]:
            with self.subTest(
                sleep=sleep_enabled, activity=activity_enabled, sleeping=sleeping
            ):
                cfg = SimpleNamespace(
                    enable_sleep=sleep_enabled,
                    enable_activity=activity_enabled,
                    sleep_factor=0.0,
                    activity_factors=factors,
                )
                result = calculate_frequency(
                    base_frequency=1.0,
                    goal_type=goal_type,
                    sleeping=sleeping,
                    no_sleep_mode=False,
                    frequency_config=cfg,
                )
                self.assertEqual(result.actual_frequency, expected)

    async def test_frequency_above_one_is_not_clamped(self):
        cfg = SimpleNamespace(
            enable_sleep=True,
            enable_activity=True,
            sleep_factor=0.0,
            activity_factors=SimpleNamespace(entertainment=1.5),
        )
        result = calculate_frequency(
            base_frequency=0.9,
            goal_type="entertainment",
            sleeping=False,
            no_sleep_mode=False,
            frequency_config=cfg,
        )
        self.assertAlmostEqual(result.actual_frequency, 1.35)

    async def test_unknown_activity_and_schedule_gap_are_neutral(self):
        cfg = ScheduleFrequencyConfig()
        for goal_type in ("", "third_party_activity"):
            result = calculate_frequency(
                base_frequency=0.8,
                goal_type=goal_type,
                sleeping=False,
                no_sleep_mode=False,
                frequency_config=cfg,
            )
            self.assertEqual(result.activity_factor, 1.0)
            self.assertEqual(result.actual_frequency, 0.8)

    async def test_runtime_sync_handles_activity_sleep_and_disabled_modes(self):
        frequency = FakeFrequency()
        cfg = SimpleNamespace(
            no_sleep_mode=False,
            frequency=ScheduleFrequencyConfig(
                sleep_factor=0.2,
                activity_factors={"entertainment": 1.5},
            ),
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=cfg),
            ctx=SimpleNamespace(chat=FakeChat(), frequency=frequency),
            is_sleeping_now=lambda: False,
        )
        service = ProactiveService.__new__(ProactiveService)
        service._plugin = plugin
        plugin._frequency = plugin.ctx.frequency
        service._current_factor = {}

        activity = ("看电影", "entertainment", 60, 120, {})
        await service._sync_frequency(sleeping=False, activity=activity)
        self.assertEqual(frequency.calls[-2:], [("one", 1.5), ("two", 1.5)])

        await service._sync_frequency(sleeping=True, activity=activity)
        self.assertEqual(frequency.calls[-2:], [("one", 0.2), ("two", 0.2)])

        cfg.frequency.enable_sleep = False
        cfg.frequency.enable_activity = False
        await service._sync_frequency(sleeping=True, activity=activity)
        self.assertEqual(frequency.calls[-2:], [("one", 1.0), ("two", 1.0)])

    async def test_no_sleep_mode_never_applies_sleep_factor_and_stop_restores(self):
        frequency = FakeFrequency()
        cfg = SimpleNamespace(
            no_sleep_mode=True,
            frequency=ScheduleFrequencyConfig(sleep_factor=0.0),
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(schedule=cfg),
            ctx=SimpleNamespace(chat=FakeChat(), frequency=frequency),
            is_sleeping_now=lambda: True,
        )
        service = ProactiveService.__new__(ProactiveService)
        service._plugin = plugin
        plugin._frequency = plugin.ctx.frequency
        service._current_factor = {}
        service._stop_event = asyncio.Event()

        await service._sync_frequency(sleeping=True)
        self.assertEqual(service._current_factor, {"one": 1.0, "two": 1.0})
        service._current_factor = {"one": 1.0, "two": 1.5}
        await service.stop()
        self.assertEqual(frequency.calls[-2:], [("one", 1.0), ("two", 1.0)])
        self.assertEqual(service._current_factor, {})

    async def test_empty_frequency_streams_means_all_and_removed_stream_is_restored(self):
        plugin = SimpleNamespace(
            ctx=SimpleNamespace(chat=FakeChat(), frequency=FakeFrequency())
        )
        service = ProactiveService.__new__(ProactiveService)
        service._plugin = plugin
        plugin._frequency = plugin.ctx.frequency
        service._current_factor = {"old": 0.3}
        streams = await service._get_frequency_streams([])
        self.assertEqual(streams, ["one", "two"])
        await service._set_adjust(["old"], 1.0, "removed")
        self.assertNotIn("old", service._current_factor)
        self.assertEqual(plugin.ctx.frequency.calls, [("old", 1.0)])

    async def test_new_stream_discovery_and_whitelist_change(self):
        class MutableChat:
            def __init__(self):
                self.streams = [{"session_id": "one"}]

            async def get_all_streams(self):
                return self.streams

        chat = MutableChat()
        frequency = FakeFrequency()
        frequency_cfg = ScheduleFrequencyConfig(
            frequency_streams=[],
            activity_factors={"study": 0.3},
        )
        plugin = SimpleNamespace(
            config=SimpleNamespace(
                schedule=SimpleNamespace(no_sleep_mode=False, frequency=frequency_cfg)
            ),
            ctx=SimpleNamespace(chat=chat, frequency=frequency),
            is_sleeping_now=lambda: False,
        )
        service = ProactiveService.__new__(ProactiveService)
        service._plugin = plugin
        plugin._frequency = plugin.ctx.frequency
        service._current_factor = {}
        activity = ("读书", "study", 60, 120, {})

        await service._sync_frequency(sleeping=False, activity=activity)
        chat.streams.append({"session_id": "two"})
        await service._sync_frequency(sleeping=False, activity=activity)
        self.assertIn(("two", 0.3), frequency.calls)

        frequency_cfg.frequency_streams = ["session:two"]
        await service._sync_frequency(sleeping=False, activity=activity)
        self.assertIn(("one", 1.0), frequency.calls)
        self.assertEqual(service._current_factor, {"two": 0.3})


if __name__ == "__main__":
    unittest.main()
