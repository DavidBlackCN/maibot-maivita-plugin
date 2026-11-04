import asyncio
import unittest
from unittest.mock import AsyncMock,patch
import test_i1_development as support


class WebUITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=support.DevelopmentTests.asyncSetUp
    asyncTearDown=support.DevelopmentTests.asyncTearDown

    async def load(self):
        config=self.plugin.config.model_dump();config['availability']={'adjust_group_frequency':False}
        self.plugin.set_plugin_config(config);await self.plugin.on_load()
        return self.plugin.web

    async def test_reads_do_not_write_and_request_id_is_idempotent(self):
        web=await self.load();r=self.plugin.runtime
        with r.database.read_view() as u:before=list(u.connection.iterdump())
        web.snapshot();web.snapshot()
        with r.database.read_view() as u:self.assertEqual(list(u.connection.iterdump()),before)
        first=web.submit('sleep','same-request',30)
        self.assertEqual(web.submit('sleep','same-request',30),first)
        with self.assertRaisesRegex(ValueError,'不同请求'):web.submit('sleep','same-request',60)
        await asyncio.gather(*web.tasks)
        self.assertEqual(len(web.snapshot()['sleep']),1)
        self.assertEqual(web.submit('sleep','same-request',30)['status'],'success')

    async def test_unload_waits_for_ui_task_and_keeps_unknown(self):
        web=await self.load();entered=asyncio.Event()
        async def blocked(*args):entered.set();await asyncio.Event().wait()
        with patch.object(self.plugin.runtime,'request_replan',AsyncMock(side_effect=blocked)):
            web.submit('replan','cancelled-request');await entered.wait()
            await web.close()
        self.assertFalse(web.tasks)
        self.assertEqual(web.snapshot()['jobs'][0]['status'],'unknown')
        with self.assertRaisesRegex(ValueError,'未就绪'):web.submit('replan','after-close')

    async def test_invalid_input_and_private_note_cannot_be_modified(self):
        web=await self.load()
        with self.assertRaises(ValueError):web.submit('sleep','invalid-time',True)
        with self.assertRaises(ValueError):web.submit('unknown','invalid-action')
        web.submit('end_note','unknown-note',activity_id='not-public')
        await asyncio.gather(*web.tasks)
        self.assertEqual(web.snapshot()['jobs'][0]['status'],'failed')
