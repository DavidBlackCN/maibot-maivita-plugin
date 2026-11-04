"""Optional community calendar facts, scoped to the character's civil day."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo

DATE_API='github.xiexiaojia780.date-context-plugin.date'
WEEKDAYS=('星期一','星期二','星期三','星期四','星期五','星期六','星期日')


class Calendar:
    def __init__(self,ctx):
        self.ctx=ctx
        self.cached=None
        self.expires=None
        self.lock=asyncio.Lock()

    def snapshot(self,now,timezone):
        local=now.astimezone(ZoneInfo(timezone))
        if self.cached and self.cached['date']==local.date().isoformat() and self.cached['timezone']==timezone and now<self.expires:
            return deepcopy(self.cached)
        return {'date':local.date().isoformat(),'weekday':WEEKDAYS[local.weekday()], 'timezone':timezone,
                'calendar_status':'unknown','festivals':[],'statutory':None,'note':'未取得节假日数据，不推断放假或补班。'}

    async def get(self,now,timezone):
        async with self.lock:
            base=self.snapshot(now,timezone)
            if self.cached==base and now<self.expires:
                return base
            try:
                value=await asyncio.wait_for(self.ctx.api.call(DATE_API,timezone=timezone,include_statutory_holidays=True),3)
                if not isinstance(value,dict) or value.get('error') or value.get('success') is False or value.get('date')!=base['date']:
                    raise ValueError('日期返回无效或不匹配')
                statutory=value.get('statutory')
                # Missing calendar coverage is not equivalent to a workday.
                if not isinstance(statutory,dict) or not all(type(statutory.get(k)) is bool for k in ('on_holiday','is_makeup_workday')):
                    raise ValueError('节假日数据不完整')
                festivals=value.get('festival_names',[])
                # Upstream 1.4.6 reports false/false even outside data coverage.
                # Positive facts are useful; negative flags alone cannot prove a workday.
                positive=statutory.get('available') is not False and any(statutory[k] for k in ('on_holiday','is_makeup_workday'))
                base.update(calendar_status='available',festivals=[s[:80] for s in festivals[:12] if isinstance(s,str)] if isinstance(festivals,list) else [],
                    statutory={k:statutory[k] for k in ('on_holiday','is_makeup_workday')} if positive else None,
                    note='日历是参考；按角色身份和个人安排决定活动。' if positive else '日期插件未提供明确放假或补班依据，按星期和个人安排参考，不断言法定状态。')
            except (Exception,):
                base['calendar_status']='unavailable'
            local=now.astimezone(ZoneInfo(timezone))
            midnight=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
            self.cached=deepcopy(base)
            self.expires=min(midnight,now+timedelta(hours=24) if base['calendar_status']=='available' else now+timedelta(minutes=5))
            return base
