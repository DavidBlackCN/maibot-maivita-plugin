"""Stage one: timed daily plans, advisory quality checks and config-only weeks."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import r0_support
from maibot_autonomous_planning_plugin.maivita.host.command_status import render_status, snapshot
from maibot_autonomous_planning_plugin.maivita.host.config import ModelSettings, RoutineSettings, WeeklyPattern
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
from maibot_autonomous_planning_plugin.maivita.host.model_port import PLANNING_CONTRACT, PRESENT_CONTRACT, SDKModelPort
from maibot_autonomous_planning_plugin.maivita.life.domain.clock import ManualClock
from maibot_autonomous_planning_plugin.maivita.life.life_state.runtime import DevelopmentRuntime, LifeRuntimeConfig
from maibot_autonomous_planning_plugin.maivita.life.planning.hybrid import validate_plan_quality, validate_task_candidate
from maibot_autonomous_planning_plugin.maivita.life.storage.life_database import LifeDatabase


def proposal(items):
    return {'kind': 'apply_plan_patch', 'payload': {
        'local_date': '2026-10-29', 'upserts': items, 'reason': '测试每日规划'}}


def unscheduled(count):
    return [{'item_id': f'flexible-{i}', 'name': '随时阅读', 'time_mode': 'unscheduled',
             'visibility': 'public_summary'} for i in range(count)]


class PlanningContractTests(unittest.TestCase):
    def test_example_is_valid_timed_full_day_plan(self):
        example = deepcopy(PLANNING_CONTRACT['example'])
        self.assertEqual(validate_task_candidate(example, 'planning'), example)
        self.assertIsNone(validate_plan_quality(example))
        items = example['payload']['upserts']
        self.assertTrue(6 <= len(items) <= 12)
        self.assertEqual(items[0]['time_mode'], 'fixed')
        self.assertGreaterEqual(sum(i['time_mode'] in ('fixed', 'window') for i in items), 3)
        starts = [datetime.fromisoformat(i.get('start_at') or i['window_start_at']) for i in items]
        self.assertTrue(any(t.hour < 12 for t in starts))
        self.assertTrue(any(12 <= t.hour < 18 for t in starts))
        self.assertTrue(any(t.hour >= 18 for t in starts))
        fixed = sorted((i for i in items if i['time_mode'] == 'fixed'), key=lambda i: i['start_at'])
        self.assertTrue(all(a['end_at'] <= b['start_at'] for a, b in zip(fixed, fixed[1:])))
        self.assertIn('6-12', PLANNING_CONTRACT['instruction'])
        self.assertIn('fixed', PLANNING_CONTRACT['instruction'])
        self.assertIn('不要照抄', PLANNING_CONTRACT['instruction'])
        self.assertEqual(set(PLANNING_CONTRACT['commands']), {'apply_plan_patch'})

    def test_all_unscheduled_is_degraded_but_schema_valid(self):
        value = proposal(unscheduled(6))
        self.assertEqual(validate_task_candidate(value, 'planning'), value)
        self.assertEqual(validate_plan_quality(value), 'degraded:all_unscheduled')
        # Omitted time_mode defaults to unscheduled in ScheduleItem.
        del value['payload']['upserts'][0]['time_mode']
        self.assertEqual(validate_plan_quality(value), 'degraded:all_unscheduled')

    def test_timed_minimum(self):
        for count in (1, 2, 3, 6):
            with self.subTest(count=count):
                items = deepcopy(PLANNING_CONTRACT['example']['payload']['upserts'][:count])
                value = proposal(items + unscheduled(1))
                self.assertEqual(validate_task_candidate(value, 'planning'), value)
                self.assertEqual(validate_plan_quality(value),
                                 'degraded:insufficient_timed_items' if count < 3 else None)

    def test_out_of_range_warns_without_rejection(self):
        for count in (0, 13):
            with self.subTest(count=count):
                value = proposal(unscheduled(count))
                self.assertEqual(validate_task_candidate(value, 'planning'), value)
                with self.assertLogs(level='WARNING') as logs:
                    diagnostic = validate_plan_quality(value)
                self.assertIn('warning:item_count_out_of_range', diagnostic)
                self.assertIn(str(count), logs.output[0])
        self.assertNotIn('warning:', validate_plan_quality(proposal(unscheduled(12))))
        self.assertIsNone(validate_plan_quality({'kind': 'update_direction', 'payload': {'goals': [], 'reason': '休息'}}))

    def test_weekly_defaults_and_single_day_toml_roundtrip(self):
        days = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
        self.assertEqual(WeeklyPattern().model_dump(), dict.fromkeys(days, ''))
        self.assertEqual(RoutineSettings().weekly_pattern, WeeklyPattern())
        data = tomllib.loads('[routine.weekly_pattern]\nmonday = "上午上课，下午自由安排"\n')
        config = IntegrationConfig.model_validate(data)
        self.assertEqual(config.routine.weekly_pattern.monday, '上午上课，下午自由安排')
        self.assertTrue(all(getattr(config.routine.weekly_pattern, day) == '' for day in days[1:]))
        self.assertEqual(config.selected_config().routine, config.routine)
        self.assertEqual(IntegrationConfig.model_validate(config.model_dump()), config)
        self.assertEqual(RoutineSettings().weekly_pattern.monday, '')
        example = tomllib.loads((Path(__file__).resolve().parents[1] / 'config.example.toml').read_text())
        self.assertEqual(example['routine']['weekly_pattern'], dict.fromkeys(days, ''))
        self.assertEqual(IntegrationConfig.model_validate(example).routine.weekly_pattern, WeeklyPattern())
        with self.assertRaises(ValueError): WeeklyPattern(monday=123)
        with self.assertRaises(ValueError): WeeklyPattern(monday_typo='上课')


class PlanningPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_examples_follow_local_date_timezone_and_do_not_mutate_contract(self):
        original = deepcopy(PLANNING_CONTRACT)
        state = SimpleNamespace(state_version=0, plan_version=0, config_revision=1)
        # Include local/UTC date differences and the New York DST transition day.
        cases = [('Asia/Shanghai', datetime(2027, 1, 1, 18, tzinfo=timezone.utc)),
                 ('America/New_York', datetime(2026, 11, 1, 4, tzinfo=timezone.utc))]
        for zone, now in cases:
            for task_kind in ('planning', 'present'):
                with self.subTest(zone=zone, task_kind=task_kind):
                    ctx = SimpleNamespace(llm=SimpleNamespace(generate=AsyncMock(return_value={
                        'success': True, 'response': json.dumps(original['example'])})),
                        config=SimpleNamespace(get=AsyncMock(return_value='')))
                    port = SDKModelPort(ctx, ModelSettings(),
                        context=lambda: {'now': now, 'character_timezone': zone},
                        routine=RoutineSettings(weekly_pattern={'monday': 'WEEKLY_NOT_IN_PROMPT'}))
                    await port(state, (), task_kind=task_kind)
                    prompt_text = ctx.llm.generate.call_args.args[0]
                    prompt = json.loads(prompt_text)
                    self.assertNotIn('weekly_pattern', prompt_text)
                    self.assertNotIn('WEEKLY_NOT_IN_PROMPT', prompt_text)
                    if task_kind == 'present':
                        self.assertEqual(prompt['contract'], PRESENT_CONTRACT)
                        continue
                    example = prompt['contract']['example']
                    today = now.astimezone(ZoneInfo(zone)).date()
                    self.assertEqual(example['payload']['local_date'], today.isoformat())
                    validate_task_candidate(example, 'planning')
                    for item in example['payload']['upserts']:
                        for key in ('start_at', 'end_at', 'window_start_at', 'window_end_at'):
                            if key not in item: continue
                            instant = datetime.fromisoformat(item[key])
                            self.assertEqual(instant.date(), today)
                            self.assertEqual(instant.utcoffset(), instant.astimezone(ZoneInfo(zone)).utcoffset())
                    self.assertEqual(example['payload']['upserts'][0]['start_at'][11:16], '08:00')
        self.assertEqual(PLANNING_CONTRACT, original)


class PlanningQualityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_quality_persists_without_retry_or_execution_and_status_counts_schedule_items(self):
        timed = deepcopy(PLANNING_CONTRACT['example']['payload']['upserts'])
        cases = [(unscheduled(1), 'planning', 'degraded:all_unscheduled'),
                 (timed[:2], 'planning', 'degraded:insufficient_timed_items'),
                 (timed, 'planning', None),
                 ([], 'planning', 'warning:item_count_out_of_range'),
                 (unscheduled(13), 'planning', 'degraded:all_unscheduled;warning:item_count_out_of_range'),
                 (timed + unscheduled(7), 'planning', 'warning:item_count_out_of_range'),
                 (unscheduled(1), 'present', None)]
        for items, task_kind, diagnostic in cases:
            with self.subTest(count=len(items), task_kind=task_kind, diagnostic=diagnostic), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                clock = ManualClock(datetime(2026, 10, 29, 0, tzinfo=timezone.utc))
                db = LifeDatabase.create(root / 'life.db', clock=clock)
                model = AsyncMock(return_value=proposal(items))
                runtime = DevelopmentRuntime(db, model, config=LifeRuntimeConfig(), lock_path=root / 'lock').start()
                try:
                    trigger = 'daily-planning:2026-10-29' if task_kind == 'planning' else 'activity-check'
                    result = await runtime.request_replan(trigger)
                    self.assertEqual(result['status'], 'applied')
                    self.assertEqual(model.await_count, 1)
                    self.assertIsNone(runtime.engine.state().primary_activity_id)
                    with db.read_view() as u:
                        run = u.connection.execute('SELECT * FROM decision_runs').fetchone()
                        self.assertEqual(run['status'], 'applied')
                        self.assertEqual(run['diagnostic'], diagnostic)
                        self.assertEqual(json.loads(run['candidate_json'])['kind'], 'apply_plan_patch')
                        self.assertEqual(u.connection.execute('SELECT count(*) FROM schedule_items').fetchone()[0], len(items))
                        self.assertEqual(u.connection.execute('SELECT count(*) FROM activities').fetchone()[0], 0)
                    # The durable quality signal survives restart and feeds /maivita status.
                    await runtime.close()
                    runtime = DevelopmentRuntime(db, model, config=LifeRuntimeConfig(), lock_path=root / 'lock').start()
                    data = snapshot(runtime, 'Asia/Shanghai', 'test-stream')
                    degraded = bool(diagnostic and diagnostic.startswith('degraded:'))
                    self.assertEqual(data['degraded'], degraded)
                    self.assertEqual(result.get('degraded', False), degraded)
                    self.assertEqual(sum(data['counts'].values()), len(items))
                    self.assertIn(f'今日参考计划：{len(items)} 项', render_status(data))
                finally:
                    await runtime.close()
