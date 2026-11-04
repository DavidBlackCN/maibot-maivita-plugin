"""Single-writer integration with the official shared frequency slot.

Readback detects observed conflicts, not atomic ownership. Do not combine with
another frequency writer. Persist before RPC; unload never unconditionally resets.
"""
import asyncio
import math
from ..life.domain.clock import to_epoch_ms
from .config import stream_allowed


class LifeFrequency:
    def __init__(self,plugin):
        self.plugin=plugin
        self.status='等待检查'
        self.stop=asyncio.Event()
        self.lock=asyncio.Lock()

    def claims(self):
        with self.plugin.runtime.database.read_view() as u:
            return {r['stream_id']:dict(r) for r in u.connection.execute('SELECT * FROM frequency_claims')}

    def save(self,stream,original,last,status):
        with self.plugin.runtime.database.unit_of_work() as u:
            u.connection.execute('INSERT OR REPLACE INTO frequency_claims VALUES(?,?,?,?,?)',
                (stream,original,last,status,to_epoch_ms(self.plugin.runtime.engine.clock.now_utc())))
            u.commit()

    def forget(self,stream):
        with self.plugin.runtime.database.unit_of_work() as u:
            u.connection.execute('DELETE FROM frequency_claims WHERE stream_id=?',(stream,));u.commit()

    async def value(self,stream):
        value=await asyncio.wait_for(self.plugin.ctx.frequency.get_adjust(stream),2)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError('频率读数不可用')
        return value

    async def write(self,stream,value):
        result=await asyncio.wait_for(self.plugin.ctx.frequency.set_adjust(stream,value),2)
        if isinstance(result,dict) and result.get('success') is False:
            raise ValueError('频率写入失败')
        return await self.value(stream)

    async def restore(self,stream,row):
        current=await self.value(stream)
        if row['status']=='conflict' or current not in (row['last_written'],row['original']):
            self.status='检测到其他频率修改，保留其值'
            self.forget(stream)
            return
        if current!=row['original']:
            self.save(stream,row['original'],row['last_written'],'restoring')
            if await self.write(stream,row['original'])!=row['original']:
                raise ValueError('频率恢复未确认')
        self.forget(stream)

    async def tick(self):
        async with self.lock:
            p=self.plugin
            rows=await asyncio.wait_for(p.ctx.chat.get_all_streams(platform='qq'),2) if p.active_config.availability.adjust_group_frequency else []
            if not isinstance(rows,list):raise ValueError('会话列表不可用')
            targets={r['session_id'] for r in rows if isinstance(r,dict) and r.get('is_group_session') is True
                and isinstance(r.get('session_id'),str) and r.get('stream_id')==r['session_id']
                and stream_allowed(r['session_id'],p.active_config.instance.allowed_streams)} if p.active_config.availability.adjust_group_frequency else set()
            claims=self.claims()
            for stream,row in claims.items():
                if stream not in targets or row['status']=='restoring':await self.restore(stream,row)
            participation=p.runtime.queries.get_participation()
            p.runtime.queries.participation_previous=participation['multiplier']
            for stream in targets:
                current=await self.value(stream)
                row=self.claims().get(stream)
                if row and row['status']=='conflict':continue
                if row and current not in (row['last_written'],row['original']):
                    self.save(stream,row['original'],row['last_written'],'conflict');continue
                original=row['original'] if row else current
                target=original*participation['multiplier']
                if row is None and target==current:continue
                if row and row['status']=='active' and row['last_written']==target==current:continue
                self.save(stream,original,target,'pending')
                if current!=target:
                    observed=await self.write(stream,target)
                    if observed!=target:
                        self.status='频率尚未生效，等待会话就绪';continue
                self.save(stream,original,target,'active')
            statuses={r['status'] for r in self.claims().values()}
            self.status='其他插件修改了频率，已停止覆盖' if 'conflict' in statuses else '频率尚未生效，等待会话就绪' if 'pending' in statuses else (
                '已启用，仅影响普通群聊' if p.active_config.availability.adjust_group_frequency else '已关闭')

    async def run(self):
        while not self.stop.is_set():
            try:await self.tick()
            except Exception:self.status='频率服务暂不可用，未确认的操作保留待检查'
            try:await asyncio.wait_for(self.stop.wait(),15)
            except TimeoutError:pass

    async def close(self):
        self.stop.set()
        async with self.lock:
            for stream,row in self.claims().items():
                try:await self.restore(stream,row)
                except Exception:self.save(stream,row['original'],row['last_written'],'restoring')
