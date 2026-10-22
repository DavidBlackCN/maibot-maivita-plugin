"""T03/T04/T05: real SDK proxies and fixed Host resolver/Hook merge logic."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from r0_support import ROOT, host_classes
from maibot_sdk.context import PluginContext, PluginPaths
from maibot_autonomous_planning_plugin.maivita.legacy.plugin import MaiVitaPlugin
from maibot_autonomous_planning_plugin.maivita.legacy.config_models import MaiVitaConfig
from maibot_autonomous_planning_plugin.maivita.legacy.services.inject_service import InjectService
from maibot_autonomous_planning_plugin.maivita.integrations.safety import OwnedFrequency, ReplyPermits, PLUGIN_ID, OLD_ID


def injection_service(plugin):
    svc = InjectService(plugin)
    svc._get_current_schedule = AsyncMock(return_value=("Study", "Synthetic context", [], "study"))
    svc._compute_activity_aux = lambda *args: (None, None)
    svc._build_inject_text = lambda **kwargs: ("Synthetic current life", True, None)
    svc._inject_optimizer = svc._intent_classifier = svc._context_cache = None
    return svc


class HookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = MaiVitaPlugin()
        self.plugin.set_plugin_config({})
        self.plugin._inject_svc = injection_service(self.plugin)

    async def test_T03_both_payloads_preserve_all_unknown_fields_and_idempotence(self):
        for key, messages in [
            ("messages", [{"role": "system", "content": "other plugin", "unknown": 42}, {"role": "user", "content": "now?"}]),
            ("items", [{"item_type": "SystemMessageItem", "meta": {"item_id": "other", "logical_turn_id": "turn-1"},
                        "parts": [{"type": "text", "text": "other plugin"}]},
                       {"item_type": "UserMessageItem", "meta": {"item_id": "user"}, "parts": [{"type": "text", "text": "now?"}]}]),
        ]:
            payload = {key: messages, "session_id": "group-1", "item_schema_version": 1,
                       "tool_definitions": ["keep"], "custom_other_plugin": {"nested": True}}
            before = deepcopy(payload)
            result = await self.plugin.handle_inject_schedule(**payload)
            modified = result["modified_kwargs"]
            self.assertEqual(payload, before)
            for field in payload:
                if field != key:
                    self.assertEqual(modified[field], payload[field])
            self.assertEqual(len(modified[key]), len(messages) + 1)
            self.assertEqual(modified[key][0], messages[0])
            if key == "items":
                item = modified[key][1]
                self.assertEqual(item["item_type"], "SystemMessageItem")
                self.assertEqual(item["meta"]["logical_turn_id"], "turn-1")
                datetime.fromisoformat(item["meta"]["timestamp"])
                self.assertNotIn(item["meta"]["item_id"], {"other", "user"})
            repeated = await self.plugin.handle_inject_schedule(**modified)
            self.assertEqual(repeated, {"action": "continue"})

    async def test_T03_replyer_append_attempt_and_invalid_payload(self):
        payload = {"session_id": "private-1", "attempt": 1, "extra_prompt": "persona plugin context",
                   "task_name": "replyer", "reply_tool_args": {"keep": 1}, "model_name": "fixed"}
        modified = (await self.plugin.handle_inject_replyer(**payload))["modified_kwargs"]
        self.assertTrue(modified["extra_prompt"].startswith(payload["extra_prompt"]))
        self.assertEqual(modified["attempt"], 1)
        self.assertEqual(modified["reply_tool_args"], payload["reply_tool_args"])
        self.assertEqual(await self.plugin.handle_inject_replyer(**modified), {"action": "continue"})
        self.assertEqual(await self.plugin.handle_inject_replyer(session_id="x", attempt=2), {"action": "continue"})
        self.assertEqual(await self.plugin.handle_inject_replyer(extra_prompt={"unexpected": True}), {"action": "continue"})
        self.plugin._inject_svc._get_current_schedule.side_effect = RuntimeError("SDK failure")
        self.assertEqual(await self.plugin.handle_inject_replyer(session_id="x"), {"action": "continue"})

    async def test_T03_actual_host_whole_kwargs_replacement_keeps_other_plugin_context(self):
        host = host_classes("src/plugin_runtime/host/hook_dispatcher.py",
                            {"HookDispatcher", "HookDispatchResult", "HookHandlerExecutionResult"})
        dispatcher = host.HookDispatcher.__new__(host.HookDispatcher)
        payload = {"session_id": "g", "extra_prompt": "upstream", "attempt": 1, "unknown": 123}
        result = await self.plugin.handle_inject_replyer(**payload)
        dispatch = host.HookDispatchResult("maisaka.replyer.before_request", kwargs=payload)
        dispatcher._merge_blocking_result(
            NS(allow_kwargs_mutation=True, allow_abort=True), NS(entry=NS(full_name="maivita")),
            host.HookHandlerExecutionResult("maivita", PLUGIN_ID, modified_kwargs=result["modified_kwargs"]), dispatch)
        self.assertEqual(dispatch.kwargs["unknown"], 123)
        self.assertIn("upstream", dispatch.kwargs["extra_prompt"])
        self.assertEqual(dispatch.kwargs["extra_prompt"].count("<!-- maivita:life -->"), 1)


class SDKContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_T04_real_SDK_full_legacy_route_through_real_host_registry_and_resolver(self):
        registry_module = host_classes("src/plugin_runtime/host/api_registry.py", {"APIEntry", "APIRegistry"})
        component_module = host_classes("src/plugin_runtime/capabilities/components.py", {"RuntimeComponentCapabilityMixin"})
        plugin = MaiVitaPlugin()
        registry = registry_module.APIRegistry()
        count = registry.register_plugin_apis(PLUGIN_ID, [{**c, "component_type": c["type"]} for c in plugin.get_components()])
        self.assertEqual(count, 2)
        async def invoke_api(plugin_id, component_name, args, timeout_ms):
            self.assertEqual(plugin_id, PLUGIN_ID)
            value = await getattr(plugin, component_name)(**args)
            return NS(error=None, payload={"success": True, "result": value})
        supervisor = NS(api_registry=registry, invoke_api=invoke_api)
        manager = component_module.RuntimeComponentCapabilityMixin()
        manager.supervisors = [supervisor]
        calls = []
        async def rpc(method, plugin_id, payload, **kwargs):
            calls.append((method, plugin_id, payload))
            return await manager._cap_api_call(plugin_id, "api.call", payload["args"])
        consumer = PluginContext("synthetic.consumer", rpc_call=rpc)
        old = await consumer.api.call(OLD_ID + ".get_current_activity", chat_id="global", ignored=True)
        new = await consumer.api.call(PLUGIN_ID + ".get_current_activity", chat_id="global")
        self.assertEqual(old, new)
        self.assertEqual(old["error"], "plugin_not_initialized")
        self.assertEqual(set(old), {"has_activity", "activity", "next_activities", "as_of", "timezone", "error"})
        # A live service projection follows both routes without another writer.
        plugin._inject_svc = NS(get_current_activity_snapshot=AsyncMock(return_value={**old, "error": None, "as_of": "fixture"}))
        active_old = await consumer.api.call(OLD_ID + ".get_current_activity", chat_id="global")
        active_new = await consumer.api.call(PLUGIN_ID + ".get_current_activity", chat_id="global")
        self.assertEqual(active_old, active_new)
        self.assertEqual(active_old["as_of"], "fixture")
        self.assertTrue(calls)

    async def test_T05_real_SDK_global_frequency_is_refused_without_any_RPC_write(self):
        rpc = AsyncMock()
        ctx = PluginContext(PLUGIN_ID, rpc_call=rpc)
        adapter = OwnedFrequency(ctx.frequency)
        self.assertFalse(adapter.available)
        self.assertFalse(await adapter.set_adjust("group", 0))
        self.assertFalse(await adapter.remove_adjust("group"))
        rpc.assert_not_awaited()

    async def test_T05_owned_frequency_port_removes_only_this_adjustment(self):
        values = {("g", "other.plugin"): 0.7}
        class Capability:
            async def set_adjust(self, stream, value, *, adjustment_id):
                values[stream, adjustment_id] = value
                return True
            async def remove_adjust(self, stream, *, adjustment_id):
                values.pop((stream, adjustment_id), None)
                return True
        owned = OwnedFrequency(Capability())
        self.assertTrue(owned.available)
        await owned.set_adjust("g", 0)
        await owned.remove_adjust("g")
        self.assertEqual(values, {("g", "other.plugin"): 0.7})

    async def test_T05_receipt_token_exact_stream_once_expiry_and_concurrent_normal_send(self):
        now = [0]
        permits = ReplyPermits(clock=lambda: now[0])
        plugin = MaiVitaPlugin()
        plugin._reply_permits = permits
        plugin._loaded = True
        plugin.is_sleeping_now = lambda: True
        entered, release = asyncio.Event(), asyncio.Event()
        tokens = []
        async def operation(token):
            tokens.append(token)
            entered.set()
            await release.wait()
        task = asyncio.create_task(plugin.send_command_reply("same-stream", operation))
        await entered.wait()
        normal = await plugin.handle_sleep_silence(message={"session_id": "same-stream"})
        self.assertEqual(normal["action"], "abort")
        token = tokens[0]
        message = {"session_id": "other", "maivita_receipt_token": token, "source_plugin_id": PLUGIN_ID}
        self.assertFalse(permits.consume(message))
        message["session_id"] = "same-stream"
        message["source_plugin_id"] = "forged-other-source"
        self.assertFalse(permits.consume(message))
        message["source_plugin_id"] = PLUGIN_ID
        self.assertEqual((await plugin.handle_sleep_silence(message=message))["action"], "continue")
        self.assertFalse(permits.consume(message))
        expired = permits.issue("same-stream")
        now[0] = 11
        self.assertFalse(permits.consume({**message, "maivita_receipt_token": expired}))
        release.set()
        await task

    async def test_T05_receipt_exception_reclaims_permit_and_real_SDK_forwards_kwarg(self):
        plugin = MaiVitaPlugin()
        async def fail(token):
            raise RuntimeError("send failure")
        with self.assertRaises(RuntimeError):
            await plugin.send_command_reply("g", fail)
        self.assertEqual(plugin._reply_permits.pending, {})
        rpc = AsyncMock(return_value={"success": True})
        ctx = PluginContext(PLUGIN_ID, rpc_call=rpc)
        await plugin.send_command_reply("g", lambda token: ctx.send.text("receipt", "g", maivita_receipt_token=token))
        self.assertIn("maivita_receipt_token", rpc.call_args.args[2]["args"])


if __name__ == "__main__":
    unittest.main()
