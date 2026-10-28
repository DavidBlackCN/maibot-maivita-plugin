"""Owned persistent confirmation journal; never a cross-plugin execution API."""
from datetime import datetime,timedelta
import json,secrets,sqlite3
from ..life.domain.commands import Command
from .life_commands import parse,describe
from .management import local_request,API
from .routes import command_route
import asyncio


class Requests:
    def __init__(self,path):
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        tables=self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version not in (0,1) or (version==0 and tables):self.db.close();raise ValueError('Unsupported management journal')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS requests(token TEXT PRIMARY KEY,origin TEXT UNIQUE,actor TEXT,body TEXT,status TEXT,expires REAL,result TEXT); PRAGMA user_version=1;')
    def close(self):self.db.close()
    def get(self,token,actor):
        row=self.db.execute('SELECT * FROM requests WHERE token=? AND actor=?',(token,actor)).fetchone()
        if row is None:raise ValueError('请求不存在，或不属于当前账号与会话')
        return dict(row)
    def put_status(self,token,status,result=''):
        with self.db:self.db.execute('UPDATE requests SET status=?,result=? WHERE token=?',(status,result,token))
    def create(self,origin,actor,operation,state,now):
        row=self.db.execute('SELECT token FROM requests WHERE origin=? AND actor=?',(origin,actor)).fetchone()
        if row:
            existing=self.get(row[0],actor)
            if json.loads(existing['body']).get('text_digest')!=operation.get('text_digest'):raise ValueError('同一消息的命令内容冲突')
            return existing
        if self.db.execute('SELECT count(*) FROM requests').fetchone()[0]>=10000:raise ValueError('管理账本已满，请先备份维护；不会删除去重记录')
        if self.db.execute("SELECT count(*) FROM requests WHERE actor=? AND status='pending' AND expires>?",(actor,now.timestamp())).fetchone()[0]>=5:raise ValueError('待确认请求过多，请先确认或取消')
        token=secrets.token_hex(6)
        body={**operation,'state_version':state.state_version,'plan_version':state.plan_version,'config_revision':state.config_revision}
        with self.db:self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
            (token,origin,actor,json.dumps(body,ensure_ascii=False),'pending',(now+timedelta(minutes=2)).timestamp(),''))
        return self.get(token,actor)
    def reconcile(self,row,engine):
        if row['status']=='applying':
            with engine.database.read_view() as u:
                receipt=u.connection.execute('SELECT result_json FROM command_receipts WHERE idempotency_key=?',('managed:'+row['token'],)).fetchone()
            status='applied' if receipt else 'unknown'
            self.put_status(row['token'],status,receipt[0] if receipt else '执行结果不确定，不自动重试')
            row.update(status=status,result=receipt[0] if receipt else '执行结果不确定，不自动重试')
        return row


def result_text(row):
    status={'pending':'待确认','applied':'已执行','cancelled':'已取消','expired':'已过期','failed':'未执行','unknown':'结果不确定','applying':'处理中'}[row['status']]
    details=row['result']
    if row['status']=='applied':
        result=json.loads(details) if details else {}
        body=json.loads(row['body']);payload=body['payload']
        target=result.get('target_id') or payload.get('goal_id')
        if payload.get('upserts'):target=payload['upserts'][0]['item_id']
        lines=[]
        if target:lines.append(('目标ID：' if body['kind']=='manage_goal' else '计划条目ID：')+target)
        if result.get('primary_activity_id'):lines.append('活动ID：'+result['primary_activity_id'])
        details='\n'.join(lines)
    return f"请求 {row['token']}：{status}"+(('\n'+details) if details else '')


async def dispatch(plugin,command):
    runtime=plugin.runtime;cfg=plugin.active_config
    if not plugin.ready:return 'MaiVita 未就绪'
    try:
        operation=parse(command['text'],runtime.engine,cfg.instance.timezone)
        route=await command_route(plugin.ctx,command)
        if not plugin.ready or plugin.runtime is not runtime or plugin.active_config is not cfg:raise ValueError('配置已改变，请重新操作')
        query,origin=local_request(plugin,command,route['account_id'],management_action=operation.get('kind','inspect'))
        actor=json.dumps([query[k] for k in ('account_id','bot_account_id','route_scope','stream_id')])
        if plugin.management is None:plugin.management=Requests(runtime.engine.database.path.with_name('management.sqlite3'))
        store=plugin.management;now=runtime.engine.clock.now_utc()
        row=None
        if 'control' in operation:
            row=store.reconcile(store.get(operation['token'],actor),runtime.engine)
            if operation['control']=='cancel':
                if row['status']=='pending':store.put_status(row['token'],'cancelled');row['status']='cancelled'
                return result_text(row)
            if row['status']=='pending' and now.timestamp()>=row['expires']:
                store.put_status(row['token'],'expired');row['status']='expired'
            if operation['control']=='result' or row['status']!='pending':return result_text(row)
            candidate=json.loads(row['body']);query['action']=candidate['kind']
        else:
            import hashlib
            candidate={**operation,'text_digest':hashlib.sha256(command['text'].encode()).hexdigest()}
        response=await asyncio.wait_for(plugin.ctx.call_host_method('cap.call',payload={
            'capability':'api.call','args':{'api_name':API,'version':'1','args':query}}),3)
        decision=response.get('result') if type(response) is dict and response.get('success') is True else None
        if type(decision) is not dict or decision.get('allowed') is not True or decision.get('policy_version')!=1:
            raise ValueError('权限不足；需在 Identity 配置 vita_admin_accounts，关系称谓不授予权限')
        if not plugin.ready or plugin.runtime is not runtime or plugin.active_config is not cfg or plugin.management is not store:
            raise ValueError('配置或运行时已改变，请重新操作')
        if row is None:
            row=store.create(origin,actor,candidate,runtime.engine.state(),now)
            if row['status']!='pending':return result_text(row)
            return '请核对：'+describe(json.loads(row['body']))+'\n两分钟内发送 /maivita confirm '+row['token']+'\n取消：/maivita cancel '+row['token']
        # No await between the final status check and journal/engine writes.
        row=store.get(row['token'],actor)
        if row['status']!='pending':return result_text(store.reconcile(row,runtime.engine))
        if runtime.engine.clock.now_utc().timestamp()>=row['expires']:
            store.put_status(row['token'],'expired');return '请求已过期，请重新提交'
        command_value=Command(kind=candidate['kind'],payload=candidate['payload'],source='authorized',
            idempotency_key='managed:'+row['token'],expected_state_version=candidate['state_version'],
            expected_plan_version=candidate['plan_version'],expected_config_revision=candidate['config_revision'],
            expires_at=datetime.fromtimestamp(row['expires'],tz=now.tzinfo))
        store.put_status(row['token'],'applying')
        try:
            result=runtime.engine.execute(command_value)
            # Persist actual IDs so completed requests can be looked up later.
            result['operation']=candidate['kind'];result['target_id']=candidate['payload'].get('goal_id')
            if candidate['payload'].get('upserts'):result['target_id']=candidate['payload']['upserts'][0]['item_id']
            store.put_status(row['token'],'applied',json.dumps(result,ensure_ascii=False))
        except Exception as exc:
            recovered=store.reconcile(store.get(row['token'],actor),runtime.engine)
            if recovered['status']=='applied':return result_text(recovered)
            # A committed receipt would have been found. Failure is retained;
            # retries must be a fresh explicit request, never background work.
            store.put_status(row['token'],'failed',str(exc)[:160])
        return result_text(store.get(row['token'],actor))
    except asyncio.CancelledError:raise
    except Exception as exc:return 'MaiVita 未执行：'+str(exc)[:180]
