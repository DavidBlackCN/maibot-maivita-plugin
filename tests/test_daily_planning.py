import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
import r0_support
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import ManualClock
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime,LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.host.presentation import public_text

class DailyTests(unittest.IsolatedAsyncioTestCase):
    async def test_day_trigger_persists_and_restart_does_not_retry_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); clock=ManualClock(datetime(2026,10,28,1,tzinfo=timezone.utc))
            db=LifeDatabase.create(root/'life.db',clock=clock); calls=[]
            async def model(state,triggers):
                calls.append(triggers)
                return {'kind':'apply_plan_patch','payload':{'local_date':clock.to_local(clock.now_utc(),'Asia/Shanghai').date().isoformat(),
                    'upserts':[],'reason':'synthetic daily decision'}}
            cfg=LifeRuntimeConfig(daily_planning_enabled=True)
            runtime=DevelopmentRuntime(db,model,config=cfg,lock_path=root/'lock').start()
            try:
                runtime.tick(); runtime.tick()
                await runtime.process_triggers()
                self.assertEqual(len(calls),1)
            finally: await runtime.close()
            runtime=DevelopmentRuntime(db,model,config=cfg,lock_path=root/'lock').start()
            try:
                runtime.tick(); await runtime.process_triggers(); self.assertEqual(len(calls),1)
                clock.advance(timedelta(days=1)); runtime.tick(); await runtime.process_triggers()
                self.assertEqual(len(calls),2)
            finally: await runtime.close()

    def test_public_status_does_not_expose_internal_envelope_or_invent_energy(self):
        result={'ok':True,'data':{'sleep_phase':'awake','activity':None,'plan_version':0,'degraded_reason':'history_incomplete'}}
        text=public_text('status',result,'Asia/Shanghai')
        self.assertIn('清醒',text); self.assertIn('尚未生成',text)
        self.assertNotIn('api_version',text); self.assertNotIn('null',text)

    def test_availability_uses_actual_public_recommendation_field(self):
        for value,phrase in [(True,'可以参与聊天'),(False,'当前不宜打扰')]:
            text=public_text('availability',{'ok':True,'data':{'sleep_phase':'awake','can_reply_recommendation':value}},'Asia/Shanghai')
            self.assertIn(phrase,text)
