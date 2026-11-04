"""Private WebUI services; Host authenticates and restricts the API bindings."""
import asyncio
import json
import re
from ..life.domain.commands import Command
from ..life.domain.clock import to_epoch_ms
from .command_status import snapshot as status_snapshot


class LifeWebUI:
    def __init__(self,plugin):
        self.plugin=plugin
        self.tasks=set()
        self.closed=False
        with plugin.runtime.database.unit_of_work() as u:
            u.connection.execute("UPDATE webui_jobs SET status='unknown',result_json=? WHERE status='running'",
                (json.dumps({'message':'上次操作结果待核对，不自动重试'},ensure_ascii=False),))
            u.commit()

    def snapshot(self):
        p=self.plugin;r=p.runtime;now=r.engine.clock.now_utc()
        facts=r.queries.get_public_life_context()
        with r.database.read_view() as u:
            jobs=[dict(row) for row in u.connection.execute('SELECT * FROM webui_jobs ORDER BY created_at_ms DESC LIMIT 10')]
            usage=[dict(row) for row in u.connection.execute('SELECT * FROM model_usage ORDER BY started_at_ms DESC LIMIT 30')]
            sessions=[{'started_at':s.started_at.isoformat(),'ended_at':s.ended_at.isoformat() if s.ended_at else None,
                       'expected_wake_at':s.expected_wake_at.isoformat(),'minutes':s.actual_sleep_minutes}
                      for s in sorted(u.sessions.for_character('default'),key=lambda s:s.started_at,reverse=True)[:10]]
            runs=[dict(row) for row in u.connection.execute('SELECT status,diagnostic,started_at_ms,finished_at_ms FROM decision_runs ORDER BY started_at_ms DESC LIMIT 10')]
        for job in jobs:
            result=job.pop('result_json')
            job['result']=json.loads(result) if result else None
            job.pop('args_json')
        for entry in usage:
            tokens=entry.pop('tokens_json')
            entry['tokens']=json.loads(tokens) if tokens else None
        return {'facts':facts,'state':r.queries.get_life_state(),'plan':r.queries.get_plan(),
                'participation':r.queries.get_participation(),'status':status_snapshot(r,p.active_config.instance.timezone,''),
                'calendar':p.model_port.calendar.snapshot(now,p.active_config.instance.timezone),
                'frequency':p.frequency.status if p.frequency else '未启动',
                'frequency_enabled':p.active_config.availability.adjust_group_frequency,
                'jobs':jobs,'usage':usage,'decisions':runs,'sleep':sessions,
                'settings':p.active_config.model_dump(exclude={'instance':{'writer_lock'}})}

    def submit(self,action,request_id,minutes=30,activity_id=''):
        if self.closed or not self.plugin.ready:raise ValueError('生活服务未就绪')
        if action not in ('replan','plan','sleep','wake','end_note','abandon_note'):raise ValueError('不支持的操作')
        if not isinstance(request_id,str) or not re.fullmatch('[a-zA-Z0-9_-]{8,80}',request_id):raise ValueError('操作编号无效')
        if type(minutes) is not int or not 5<=minutes<=720:raise ValueError('睡眠时长需为5–720分钟')
        if not isinstance(activity_id,str) or len(activity_id)>80:raise ValueError('活动编号无效')
        args=json.dumps([action,minutes,activity_id])
        r=self.plugin.runtime
        with r.database.unit_of_work() as u:
            old=u.connection.execute('SELECT * FROM webui_jobs WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['args_json']!=args:raise ValueError('操作编号已用于不同请求')
                return {'request_id':request_id,'status':old['status']}
            if u.connection.execute("SELECT 1 FROM webui_jobs WHERE status='running'").fetchone():raise ValueError('已有操作进行中，请稍后再试')
            if u.connection.execute('SELECT count(*) FROM webui_jobs').fetchone()[0]>=10000:
                raise ValueError('管理操作记录已达上限，请先备份并检查数据；不会删除待核对记录')
            u.connection.execute('INSERT INTO webui_jobs VALUES(?,?,?,NULL,?)',
                (request_id,args,'running',to_epoch_ms(r.engine.clock.now_utc())))
            u.commit()
        task=asyncio.create_task(self.execute(action,request_id,minutes,activity_id))
        self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        return {'request_id':request_id,'status':'running'}

    async def execute(self,action,request_id,minutes,activity_id):
        r=self.plugin.runtime
        status='success'
        try:
            if action in ('replan','plan'):
                if action=='replan' and r.engine.state().active_sleep_session_id:raise ValueError('睡眠期间请先使用唤醒操作')
                result=await r.request_replan(('manual-planning:' if action=='plan' else 'segment:webui:')+request_id)
                status='success' if result.get('status')=='applied' else 'deferred'
            elif action in ('sleep','wake'):
                state=r.engine.state()
                result=r.engine.execute(Command(kind='start_sleep' if action=='sleep' else 'wake',
                    idempotency_key='webui:'+request_id,source='authorized',expected_state_version=state.state_version,
                    payload={'duration_minutes':minutes,'reason':'WebUI 管理员请求'} if action=='sleep' else {}))
            else:
                with r.database.unit_of_work() as u:
                    row=u.connection.execute("SELECT d.status FROM activity_details d JOIN activities a USING(activity_id) WHERE activity_id=? AND a.visibility='public_summary'",(activity_id,)).fetchone()
                    if row is None:raise ValueError('记录不存在')
                    u.connection.execute('UPDATE activity_details SET status=? WHERE activity_id=?',
                        ('ended' if action=='end_note' else 'abandoned',activity_id));u.commit()
                result={'message':'已结束接续记录；不代表完成真实任务'}
        except asyncio.CancelledError:
            status='unknown';result={'message':'操作已中断，请核对当前状态，不自动重试'}
        except Exception as exc:
            status='failed';result={'message':str(exc)[:200]}
        with r.database.unit_of_work() as u:
            u.connection.execute('UPDATE webui_jobs SET status=?,result_json=? WHERE request_id=?',
                (status,json.dumps(result,ensure_ascii=False,default=str),request_id))
            u.commit()

    async def close(self):
        self.closed=True
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
