"""Independent space destination. No group routing, Hush queries or chat material."""
import asyncio
import json
import time
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from .config import VITA
from .store import canonical

TRACE='Rabbit-Jia-Er.MaiTrace'
ADAPTATION='vita-coordinate-1'

REASONS={
    'NOT_EXECUTED':'尚未检查','DISABLED':'空间已关闭','SLEEPING':'正在睡眠，不启动新任务',
    'NOT_FREE':'当前不空闲','LIFE_UNAVAILABLE':'生活状态不可用','LIFE_STALE':'生活状态已过期',
    'NO_NEW_PUBLIC_MATERIAL':'没有新的合格公开生活素材','COOLDOWN_OR_QUOTA':'发布冷却未结束或今日额度已用完',
    'BROWSE_COOLDOWN_OR_QUOTA':'浏览冷却未结束或今日额度已用完','EMPTY_INTERACTION_WHITELIST':'互动白名单为空',
    'SHADOW_NOT_EXECUTED':'仅记录拟行动，未调用模型或空间','PROVIDER_CONFIGURATION_NOT_READY':'MaiTrace 适配或配置未就绪',
    'PROVIDER_WHITELIST_MISMATCH':'互动名单超出 MaiTrace 白名单','LIFE_CHANGED_BEFORE_START':'启动前生活状态已变化',
    'SPACE_STORAGE_FULL':'空间回执存储已满','IN_PROGRESS':'任务进行中','SPACE_RPC_UNAVAILABLE':'空间接口不可用',
    'SPACE_INVALID_RESPONSE':'空间接口返回格式无效','CANCELLED_DURING_RPC':'调用被中断，需核对 QQ',
}
OUTCOMES={'not_executed':'未执行','submitted':'进行中','success':'成功','failed':'失败','deferred':'延后','unknown':'待核对'}


def explain_reason(reason):
    prefix,separator,detail=str(reason).partition(': ')
    if separator and prefix in OUTCOMES:return OUTCOMES[prefix]+'：'+explain_reason(detail)
    return '；'.join(REASONS.get(part.strip(),part.strip()) for part in str(reason).split(';'))


def render_status(status):
    modes={'disabled':'关闭','shadow':'观察（不执行）','controlled':'自动执行'}
    lines=['空间：'+modes.get(status['mode'],status['mode']),'当前原因：'+status['reason_text']]
    for kind,label in (('post','发布'),('browse','浏览')):
        quota=status['limits'][kind]
        lines.append(f'{label}今日剩余 {quota["remaining"]} 次；额度与冷却最早允许：{quota["next_at_local"]}' if quota['enabled'] else label+'已关闭（每日上限为 0）')
    lines.append('行动时仍需满足清醒、空闲、素材或白名单条件。')
    last=status.get('last')
    if last:
        basis=last.get('basis') or {}
        if isinstance(basis,str):basis=json.loads(basis)
        source=(basis.get('activity') or {}).get('name') or ('白名单好友动态' if last.get('kind')=='browse' else '公开生活')
        source=' '.join(str(source).split())[:100]
        lines.append('最近动作：'+('发布' if last.get('kind')=='post' else '浏览')+'；素材：'+source+
            '；结果：'+OUTCOMES.get(last.get('status'),str(last.get('status'))))
        if last.get('reason'):lines.append('动作说明：'+explain_reason(last['reason']))
    return '\n'.join(lines)


def unwrap(response):
    if not isinstance(response,dict) or response.get('success') is not True:
        raise ValueError('SPACE_RPC_UNAVAILABLE')
    value=response.get('result')
    if not isinstance(value,dict): raise ValueError('SPACE_INVALID_RESPONSE')
    return value


class SpaceCoordinator:
    def __init__(self,ports,settings,store,*,wall=time.time):
        self.ports,self.config,self.store,self.wall=ports,settings.space,store,wall
        self.since=wall()  # Restart/enable never drains historical opportunities.
        self.awake=None
        self.reason='NOT_EXECUTED'
        self.last=None
        self.lock=asyncio.Lock()

    def status(self):
        now=self.wall(); local=datetime.fromtimestamp(now,ZoneInfo(self.config.timezone));day=local.date().isoformat()
        limits={}
        for kind in ('post','browse'):
            row=self.store.conn.execute('SELECT count(*),max(started) FROM space_actions WHERE kind=? AND day=?',(kind,day)).fetchone()
            last=self.store.conn.execute('SELECT max(started) FROM space_actions WHERE kind=?',(kind,)).fetchone()[0]
            limits[kind]={'enabled':getattr(self.config,kind+'_daily_limit')>0,'remaining':max(0,getattr(self.config,kind+'_daily_limit')-row[0]),
                'next_at':max(self.since if kind=='post' else self.since+self.config.browse_interval_seconds,
                              (last or 0)+getattr(self.config,kind+'_interval_seconds'))}
            if not limits[kind]['remaining']:
                midnight=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
                limits[kind]['next_at']=max(limits[kind]['next_at'],midnight)
            limits[kind]['next_at_local']=datetime.fromtimestamp(max(now,limits[kind]['next_at']),ZoneInfo(self.config.timezone)).strftime('%m-%d %H:%M %Z')
        latest=self.store.conn.execute('SELECT * FROM space_actions ORDER BY started DESC LIMIT 1').fetchone()
        return {'mode':self.config.mode,'reason':self.reason,'reason_text':explain_reason(self.reason),'limits':limits,
                'last':dict(latest) if latest else self.last}

    async def life(self):
        value=unwrap(await self.ports.api.call(VITA+'.get_public_life_context',version='1'))
        if value.get('ok') is not True or value.get('api_version')!=1: raise ValueError('LIFE_UNAVAILABLE')
        data=value['data']
        age=self.wall()-datetime.fromisoformat(data['as_of']).timestamp()
        if not -5<=age<=30: raise ValueError('LIFE_STALE')
        return data

    async def cycle(self):
        async with self.lock:
            if self.config.mode=='disabled': self.reason='DISABLED'; return
            try:
                life=await self.life()
                if not life.get('awake'):
                    self.awake=False; self.since=self.wall(); self.reason='SLEEPING'; return
                if self.awake is False: self.since=self.wall()
                self.awake=True
                if life.get('availability',{}).get('state')!='free': self.reason='NOT_FREE'; return
                status=self.status(); now=self.wall()
                # Only the newest valid observation can be considered; no queue.
                records=[life.get('activity'),*life.get('history',[])]
                records=[a for a in records if isinstance(a,dict) and a.get('visibility')=='public_summary'
                         and not a.get('continuity_unknown') and datetime.fromisoformat(a.get('ended_at') or a['started_at']).timestamp()>=self.since
                         and (a.get('ended_at') or (a.get('duration_minutes') or 0)>=15)]
                records.sort(key=lambda a:a['started_at'],reverse=True)
                activity=records[0] if records else None
                if activity and self.store.conn.execute('SELECT 1 FROM space_actions WHERE operation_id=?',('post:'+activity['activity_id'],)).fetchone():
                    activity=None
                post=status['limits']['post']; browse=status['limits']['browse']
                if activity and post['remaining'] and now>=post['next_at']:
                    kind='post'; basis={'activity':activity,'current':not bool(activity.get('ended_at'))}
                elif self.config.interaction_whitelist and browse['remaining'] and now>=browse['next_at']:
                    kind='browse'; basis={'whitelist':self.config.interaction_whitelist,'max_feeds':self.config.max_feeds}
                else:
                    self.reason='COOLDOWN_OR_QUOTA' if activity else 'NO_NEW_PUBLIC_MATERIAL'
                    if not self.config.interaction_whitelist: self.reason+='; EMPTY_INTERACTION_WHITELIST'
                    elif not browse['remaining'] or now<browse['next_at']:self.reason+='; BROWSE_COOLDOWN_OR_QUOTA'
                    return
                if self.config.mode=='shadow':
                    self.reason='SHADOW_NOT_EXECUTED'
                    self.last={'kind':kind,'basis':basis,'status':'not_executed'}
                    with self.store.conn: self.store.put('space:shadow',self.last)
                    return
                contract=unwrap(await self.ports.api.call(TRACE+'.get_space_contract',version='1'))
                if contract.get('adaptation')!=ADAPTATION or contract.get('ready') is not True:
                    self.reason='PROVIDER_CONFIGURATION_NOT_READY'; return
                if kind=='browse' and not set(self.config.interaction_whitelist)<=set(contract.get('interaction_whitelist',[])):
                    self.reason='PROVIDER_WHITELIST_MISMATCH'; return
                timeout=contract['budgets'][kind]
                if type(timeout) not in (int,float) or not 1<=timeout<=86400: raise ValueError('INVALID_SPACE_BUDGET')
                method='publish_topic_api' if kind=='post' else 'browse_once_api'
                args=({'topic':'公开生活分享','life_basis':basis} if kind=='post' else
                      {'max_feeds':self.config.max_feeds,'interaction_whitelist':self.config.interaction_whitelist})
                # Recheck after preflight, before any model/platform request.
                fresh=await self.life()
                if not fresh.get('awake') or fresh.get('availability',{}).get('state')!='free':
                    self.reason='LIFE_CHANGED_BEFORE_START'; return
                operation='post:'+activity['activity_id'] if kind=='post' else 'browse:'+uuid4().hex
                day=datetime.fromtimestamp(now,ZoneInfo(self.config.timezone)).date().isoformat()
                if self.store.conn.execute('SELECT count(*) FROM space_actions').fetchone()[0]>=self.store.max_records:
                    self.reason='SPACE_STORAGE_FULL'; return
                with self.store.conn:
                    self.store.conn.execute('INSERT INTO space_actions VALUES(?,?,?,?,?,?,?,?)',
                        (operation,kind,day,now,'submitted','RPC_STARTED',canonical(basis),'{}'))
                self.reason='IN_PROGRESS'
                try:
                    response=unwrap(await asyncio.wait_for(self.ports.api.call_space(TRACE+'.'+method,
                        timeout_seconds=timeout+15,**args),timeout+20))
                    outcome=response.get('status')
                    if outcome not in ('success','failed','deferred','unknown'): outcome='unknown'
                    reason=response.get('message',outcome)
                except asyncio.CancelledError:
                    self.finish(operation,'unknown','CANCELLED_DURING_RPC',{})
                    raise
                except Exception as exc:
                    outcome='unknown'; reason=type(exc).__name__; response={}
                self.finish(operation,outcome,reason,response)
            except asyncio.CancelledError: raise
            except Exception as exc: self.reason=str(exc)[:200]

    def finish(self,operation,status,reason,result):
        with self.store.conn:
            self.store.conn.execute('UPDATE space_actions SET status=?,reason=?,result=? WHERE operation_id=?',
                (status,str(reason)[:500],canonical(result),operation))
        self.reason=status+': '+str(reason)

    async def run(self,poll_seconds):
        while True:
            await self.cycle()
            await asyncio.sleep(poll_seconds)
