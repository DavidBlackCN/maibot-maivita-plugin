import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from maibot_autonomous_planning_plugin.maivita.legacy.planner.model_selection import (
    ConfiguredModelNotFoundError,
    ScheduleModelSelection,
)
from maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_generator import ScheduleGenerator
from maibot_autonomous_planning_plugin.maivita.legacy.planner import pinned_model_adapter


class FakeGoalManager:
    pass


class FakeLLM:
    def __init__(self, available=None, response_model=None):
        self.available = list(available or [])
        self.response_model = response_model
        self.calls = []
        self.available_calls = 0

    async def get_available_models(self):
        self.available_calls += 1
        return list(self.available)

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        model = self.response_model
        if model is None:
            model = kwargs.get("model_name") or kwargs.get("model")
        return {
            "success": True,
            "response": json.dumps({"schedule_items": [{"name": "ok"}]}),
            "model": model,
        }


def make_generator(config, llm, model_config_path=None):
    plugin = type("Plugin", (), {})()
    plugin.ctx = type("Ctx", (), {"llm": llm})()
    if model_config_path is not None:
        plugin._model_config_path = model_config_path
    return ScheduleGenerator(
        FakeGoalManager(),
        config={**config, "llm_log_enabled": False},
        plugin=plugin,
        generation_id="model-selection-test",
    )


class ModelSelectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write_model_config(directory, models):
        lines = []
        for name, identifier in models:
            lines.extend(
                [
                    "[[models]]",
                    f'name = "{name}"',
                    f'model_identifier = "{identifier}"',
                    'api_provider = "test-provider"',
                    "",
                ]
            )
        path = Path(directory) / "model_config.toml"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    async def test_task_replyer_uses_maibot_task_routing(self):
        llm = FakeLLM()
        generator = make_generator({"model_mode": "task", "model_task": "replyer"}, llm)
        with self.assertLogs(
            "maibot_autonomous_planning_plugin.maivita.legacy.planner.schedule_generator", level="INFO"
        ) as captured:
            await generator._call_llm("prompt")
        self.assertEqual(llm.calls[0]["model"], "replyer")
        self.assertEqual(llm.available_calls, 0)
        self.assertIn("model_mode=task task=replyer", "\n".join(captured.output))

    async def test_task_planner_uses_maibot_task_routing(self):
        llm = FakeLLM()
        generator = make_generator({"model_mode": "task", "model_task": "planner"}, llm)
        await generator._call_llm("prompt")
        self.assertEqual(llm.calls[0]["model"], "planner")
        self.assertEqual(llm.available_calls, 0)

    async def test_specific_model_is_validated_and_selected(self):
        llm = FakeLLM(["planner", "replyer"])
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(
                directory, [("P01-gpt-5.6-terra", "gpt-5.6-terra")]
            )
            generator = make_generator(
                {
                    "model_mode": "model",
                    "model_task": "planner",
                    "model_name": "P01-gpt-5.6-terra",
                },
                llm,
                config_path,
            )
            pinned = AsyncMock(return_value={
                "success": True,
                "response": json.dumps({"schedule_items": [{"name": "ok"}]}),
                "model_name": "P01-gpt-5.6-terra",
                "resolved_model": "P01-gpt-5.6-terra",
            })
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                await generator._call_llm("prompt")

        self.assertEqual(llm.calls, [])
        self.assertEqual(llm.available_calls, 0)
        self.assertEqual(pinned.await_args.kwargs["configured_model"], "P01-gpt-5.6-terra")
        self.assertEqual(pinned.await_args.kwargs["resolved_model"], "P01-gpt-5.6-terra")

    async def test_model_identifier_resolves_to_alias(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(
                directory, [("P01-gpt-5.6-terra", "gpt-5.6-terra")]
            )
            generator = make_generator(
                {"model_mode": "model", "model_name": "gpt-5.6-terra"},
                llm,
                config_path,
            )
            pinned = AsyncMock(return_value={
                "success": True,
                "response": json.dumps({"schedule_items": [{"name": "ok"}]}),
                "model_name": "P01-gpt-5.6-terra",
                "resolved_model": "P01-gpt-5.6-terra",
            })
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                await generator._call_llm("prompt")
        self.assertEqual(pinned.await_args.kwargs["resolved_model"], "P01-gpt-5.6-terra")

    async def test_empty_content_can_recover_structured_json_from_reasoning(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(directory, [("deepseek-v4-pro", "deepseek-v4-pro")])
            generator = make_generator(
                {"model_mode": "model", "model_name": "deepseek-v4-pro"},
                llm,
                config_path,
            )
            pinned = AsyncMock(return_value={
                "success": True,
                "response": "",
                "reasoning": "分析完成。\n```json\n{\"schedule_items\":[{\"name\":\"早餐\"}]}\n```",
                "model_name": "deepseek-v4-pro",
                "resolved_model": "deepseek-v4-pro",
            })
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                items = await generator._call_llm("prompt")

        self.assertEqual(items, [{"name": "早餐"}])

    async def test_host_ignoring_specific_model_refuses_generation(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(directory, [("expected", "provider-id")])
            generator = make_generator(
                {"model_mode": "model", "model_name": "expected"}, llm, config_path
            )
            pinned = AsyncMock(return_value={
                "success": True,
                "response": json.dumps({"schedule_items": [{"name": "ok"}]}),
                "model_name": "unexpected",
                "resolved_model": "expected",
            })
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                with self.assertRaisesRegex(ConfiguredModelNotFoundError, "actual_model=unexpected"):
                    await generator._call_llm("prompt")
        self.assertEqual(llm.calls, [])

    async def test_model_mode_requires_host_to_report_actual_model(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(directory, [("expected", "provider-id")])
            generator = make_generator(
                {"model_mode": "model", "model_name": "expected"}, llm, config_path
            )
            pinned = AsyncMock(return_value={
                "success": True,
                "response": json.dumps({"schedule_items": [{"name": "ok"}]}),
                "model_name": "",
                "resolved_model": "expected",
            })
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                with self.assertRaisesRegex(ConfiguredModelNotFoundError, "未返回实际模型名"):
                    await generator._call_llm("prompt")

    async def test_unknown_specific_model_refuses_before_generation(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_model_config(directory, [("known", "provider-id")])
            generator = make_generator(
                {"model_mode": "model", "model_name": "missing"}, llm, config_path
            )
            pinned = AsyncMock()
            with patch(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter._generate_with_pinned_model",
                pinned,
            ):
                with self.assertRaisesRegex(ConfiguredModelNotFoundError, "missing"):
                    await generator._call_llm("prompt")
        pinned.assert_not_awaited()
        self.assertEqual(llm.calls, [])

    async def test_pinned_adapter_builds_single_model_task_config(self):
        created_configs = []

        class FakeTaskConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                created_configs.append(self)

        class FakeResult:
            response = "ok"
            reasoning = ""
            model_name = "P01-gpt-5.6-terra"
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15

        class FakeOrchestrator:
            def __init__(self, task_name, request_type=""):
                self.task_name = task_name
                self.request_type = request_type
                self.model_for_task = self._get_task_config_or_raise()
                self.model_usage = {
                    model: (0, 0, 0) for model in self.model_for_task.model_list
                }

            async def generate_response_async(self, **kwargs):
                self._refresh_task_config()
                return FakeResult()

        with patch.object(
            pinned_model_adapter,
            "_load_host_runtime",
            return_value=(FakeTaskConfig, FakeOrchestrator),
        ):
            with self.assertLogs(
                "maibot_autonomous_planning_plugin.maivita.legacy.planner.pinned_model_adapter",
                level="INFO",
            ) as captured:
                result = await pinned_model_adapter._generate_with_pinned_model(
                    "prompt",
                    configured_model="gpt-5.6-terra",
                    resolved_model="P01-gpt-5.6-terra",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout_ms=180_000,
                    request_type="test.pinned",
                )

        self.assertEqual(created_configs[0].model_list, ["P01-gpt-5.6-terra"])
        self.assertEqual(created_configs[0].selection_strategy, "sequential")
        self.assertEqual(result["model_name"], "P01-gpt-5.6-terra")
        self.assertEqual(result["resolved_model"], "P01-gpt-5.6-terra")
        log_text = "\n".join(captured.output)
        self.assertIn("configured_model=gpt-5.6-terra", log_text)
        self.assertIn("resolved_model=P01-gpt-5.6-terra", log_text)
        self.assertIn("actual_model=P01-gpt-5.6-terra", log_text)

    def test_selection_rejects_incomplete_model_mode(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            ScheduleModelSelection.from_config({"model_mode": "model", "model_name": ""})


if __name__ == "__main__":
    unittest.main()
