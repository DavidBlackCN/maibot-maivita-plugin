import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from maibot_autonomous_planning_plugin.maivita.legacy.core.exceptions import LLMInvalidResponseError, LLMTimeoutError
from maibot_autonomous_planning_plugin.maivita.legacy.core.models import ScheduleType
from maibot_autonomous_planning_plugin.maivita.legacy.planner.generation_coordinator import (
    GenerationInProgressError,
    ScheduleGenerationCoordinator,
)
from maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_generator import ScheduleGenerator
from maibot_autonomous_planning_plugin.maivita.legacy.services.command_service import CommandService


class GenerationCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.coordinator = ScheduleGenerationCoordinator()

    async def asyncTearDown(self):
        await self.coordinator.stop()

    async def _wait_done(self):
        for _ in range(100):
            if not self.coordinator.is_running:
                return
            await asyncio.sleep(0)
        self.fail("background generation did not finish")

    async def test_long_response_is_backgrounded_and_duplicate_is_rejected(self):
        """用未完成 Event 代表 70 秒长响应，启动调用本身不得等待它。"""
        release = asyncio.Event()
        calls = 0

        async def slow_operation(_generation_id):
            nonlocal calls
            calls += 1
            await release.wait()
            return "done"

        accepted, generation_id = self.coordinator.start_background(
            slow_operation, source="command.regenerate"
        )
        self.assertTrue(accepted)
        self.assertTrue(generation_id)

        accepted_again, same_id = self.coordinator.start_background(
            slow_operation, source="command.regenerate"
        )
        self.assertFalse(accepted_again)
        self.assertEqual(same_id, generation_id)
        self.assertEqual(calls, 0)  # start_background 没有同步等待 LLM

        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        await self._wait_done()
        self.assertEqual(self.coordinator.get_status()["state"], "success")

    async def test_auto_scheduler_and_manual_share_one_global_chain(self):
        release = asyncio.Event()

        async def automatic(_generation_id):
            await release.wait()

        accepted, generation_id = self.coordinator.start_background(
            automatic, source="auto_scheduler"
        )
        self.assertTrue(accepted)

        with self.assertRaises(GenerationInProgressError) as raised:
            await self.coordinator.run_exclusive(
                lambda _generation_id: asyncio.sleep(0),
                source="command.regenerate",
            )
        self.assertEqual(raised.exception.generation_id, generation_id)
        release.set()
        await self._wait_done()

    async def test_timeout_releases_lock_for_next_generation(self):
        async def timeout(_generation_id):
            raise LLMTimeoutError("cap.call timeout", timeout_seconds=180)

        with self.assertRaises(LLMTimeoutError):
            await self.coordinator.run_exclusive(timeout, source="tool")
        self.assertFalse(self.coordinator.is_running)

        result = await self.coordinator.run_exclusive(
            lambda _generation_id: asyncio.sleep(0, result="ok"), source="tool"
        )
        self.assertEqual(result, "ok")

    async def test_cancellation_stops_current_chain(self):
        entered = asyncio.Event()
        continued = False

        async def operation(_generation_id):
            nonlocal continued
            entered.set()
            await asyncio.Event().wait()
            continued = True

        self.coordinator.start_background(operation, source="command.regenerate")
        await entered.wait()
        await self.coordinator.stop()
        self.assertFalse(continued)
        self.assertEqual(self.coordinator.get_status()["state"], "cancelled")

    async def test_background_exception_has_traceback_and_failed_state(self):
        async def broken(_generation_id):
            raise RuntimeError("background boom")

        with self.assertLogs(
            "maibot_autonomous_planning_plugin.maivita.legacy.planner.generation_coordinator",
            level="ERROR",
        ) as captured:
            self.coordinator.start_background(broken, source="command.regenerate")
            await self._wait_done()

        output = "\n".join(captured.output)
        self.assertIn("background boom", output)
        self.assertIn("Traceback", output)
        self.assertEqual(self.coordinator.get_status()["state"], "failed")


class FakeGoalManager:
    pass


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class ScheduleGeneratorTimeoutTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(responses):
        plugin = type("Plugin", (), {})()
        plugin.ctx = type("Ctx", (), {"llm": FakeLLM(responses)})()
        return plugin

    async def test_provider_timeout_retry_keeps_generation_id_and_is_serial(self):
        valid = json.dumps({"schedule_items": [{"name": "ok"}]})
        plugin = self._plugin([
            {"success": False, "response": "APITimeoutError: provider timeout"},
            {"success": True, "response": valid},
        ])
        generator = ScheduleGenerator(
            FakeGoalManager(),
            config={
                "generation_timeout": 180,
                "generation_max_retries": 1,
                "generation_retry_backoff": 0,
                "llm_log_enabled": False,
            },
            plugin=plugin,
            generation_id="fixed-generation-id",
        )

        with self.assertLogs(
            "maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_generator",
            level="INFO",
        ) as captured:
            items = await generator._call_llm("prompt", round_num=1)

        self.assertEqual(items, [{"name": "ok"}])
        self.assertEqual(len(plugin.ctx.llm.calls), 2)
        output = "\n".join(captured.output)
        self.assertGreaterEqual(output.count("generation_id=fixed-generation-id"), 4)
        self.assertIn("retry=0", output)
        self.assertIn("retry=1", output)

    async def test_invalid_json_uses_next_quality_round(self):
        plugin = self._plugin([])
        generator = ScheduleGenerator(
            FakeGoalManager(),
            config={
                "use_multi_round": True,
                "max_rounds": 2,
                "quality_threshold": 0.5,
                "no_sleep_mode": True,
                "llm_log_enabled": False,
            },
            plugin=plugin,
            generation_id="invalid-json-retry",
        )
        valid_items = [{
            "name": "早餐",
            "description": "",
            "goal_type": "meal",
            "priority": "medium",
            "time_slot": "08:00",
            "duration_hours": 0.5,
        }]
        generator._call_llm = AsyncMock(side_effect=[
            LLMInvalidResponseError("响应正文为空"),
            valid_items,
        ])
        generator.quality_scorer.calculate_score = lambda _items, _warnings: 1.0

        result = await generator._generate_with_multi_round(
            ScheduleType.DAILY, "user", "global", {}
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "早餐")
        self.assertEqual(generator._call_llm.await_count, 2)

    async def test_capability_timeout_uses_configured_seconds_and_does_not_retry(self):
        plugin = self._plugin([
            {"success": False, "response": "[E_TIMEOUT] 请求 cap.call 超时 (123000ms)"},
        ])
        generator = ScheduleGenerator(
            FakeGoalManager(),
            config={
                "generation_timeout": 123,
                "generation_max_retries": 3,
                "generation_retry_backoff": 0,
                "llm_log_enabled": False,
            },
            plugin=plugin,
        )

        with self.assertRaises(LLMTimeoutError) as raised:
            await generator._call_llm("prompt", round_num=1)

        self.assertEqual(raised.exception.timeout_seconds, 123.0)
        self.assertEqual(raised.exception.layer, "capability")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(plugin.ctx.llm.calls), 1)

    async def test_cancelled_llm_does_not_retry(self):
        entered = asyncio.Event()

        class BlockingLLM:
            def __init__(self):
                self.calls = 0

            async def generate(self, **_kwargs):
                self.calls += 1
                entered.set()
                await asyncio.Event().wait()

        plugin = type("Plugin", (), {})()
        plugin.ctx = type("Ctx", (), {"llm": BlockingLLM()})()
        generator = ScheduleGenerator(
            FakeGoalManager(),
            config={
                "generation_timeout": 180,
                "generation_max_retries": 3,
                "generation_retry_backoff": 0,
                "llm_log_enabled": False,
            },
            plugin=plugin,
        )

        task = asyncio.create_task(generator._call_llm("prompt", round_num=1))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(plugin.ctx.llm.calls, 1)


class CommandBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_regenerate_command_returns_after_background_admission(self):
        class FakeTools:
            def __init__(self):
                self.args = None

            def start_regenerate_today_schedule(self, **kwargs):
                self.args = kwargs
                return True, "generation-command-test"

        class FakeSend:
            def __init__(self):
                self.messages = []

            async def text(self, text, stream_id, **kwargs):
                self.messages.append((text, stream_id))

        plugin = type("Plugin", (), {})()
        plugin._tools_svc = FakeTools()
        plugin.ctx = type("Ctx", (), {"send": FakeSend()})()
        service = CommandService(plugin)

        await service._handle_regenerate(
            "stream-1", ["/plan", "regenerate", "加入下午跑步"]
        )

        self.assertEqual(
            plugin._tools_svc.args,
            {"extra_prompt": "加入下午跑步", "stream_id": "stream-1"},
        )
        self.assertEqual(len(plugin.ctx.send.messages), 1)
        self.assertIn("已开始在后台", plugin.ctx.send.messages[0][0])
        self.assertIn("generation-command-test", plugin.ctx.send.messages[0][0])


if __name__ == "__main__":
    unittest.main()
