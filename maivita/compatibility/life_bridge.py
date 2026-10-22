"""Development bridge. It never opens GoalManager or writes the old database.

The R0 plugin lifecycle is retained until real takeover gates are met. This
adapter can be exercised by an isolated Runner without running both engines.
"""
from datetime import date,datetime,time,timedelta
import json
from uuid import uuid4

from ..life.domain.clock import iso_instant, to_local,resolve_local
from ..life.domain.commands import Command
from ..life.domain.models import require
from ..life.life_state.projections import LifeQueries, QueryScope


class LegacyLifeBridge:
    def __init__(self, engine, *, timezone='Asia/Shanghai'):
        self.engine,self.timezone = engine,timezone
        self.queries=LifeQueries(engine)

    def apply_generated_plan(self, payload, *, key, expected_state_version=None):
        state=self.engine.state()
        return self.engine.execute(Command(idempotency_key=key,kind='apply_plan_patch',payload=payload,
            source='legacy_observed',expected_state_version=state.state_version if expected_state_version is None else expected_state_version))

    def convert_schedule(self, schedule_data, *, local_date, key):
        """Convert verified v5.1 export fields to a proposal, never actuals."""
        from ..shared.utils.time_utils import item_time_range
        from ..life.life_state.engine import digest
        from ..life.domain.models import number
        data=json.loads(schedule_data) if isinstance(schedule_data,str) else schedule_data
        require(isinstance(data,dict) and isinstance(data.get('items'),list),'invalid legacy schedule export')
        require(data.get('schedule_type') in (None,'daily'),'weekly/monthly export needs explicit day mapping')
        day=date.fromisoformat(local_date)
        midnight=datetime.combine(day,time())
        items=[]
        for index,old in enumerate(data['items']):
            require(not old.get('parameters',{}).get('is_commitment'), 'legacy commitment link requires explicit reconciliation')
            item={'item_id':'legacy-tool-'+digest({'key':key,'index':index}), 'name':old['name'],
                  'category':old.get('goal_type','other'),'priority':old.get('priority','medium')}
            if old.get('time_slot') is not None:
                number(old.get('duration_hours'),0,24)
                interval=item_time_range(old['time_slot'],old['duration_hours'])
                item.update(time_mode='fixed',start_at=iso_instant(resolve_local(midnight+timedelta(minutes=interval.start_minute),self.timezone)),
                    end_at=iso_instant(resolve_local(midnight+timedelta(minutes=interval.end_minute),self.timezone)))
            else: item['time_mode']='unscheduled'
            items.append(item)
        return {'local_date':local_date,'reason':'legacy tool proposal','upserts':items}

    def observe_fixed_sleep(self, sleeping, *, window_end=None):
        state=self.engine.state()
        if sleeping and state.active_sleep_session_id is None:
            require(window_end is not None and window_end > self.engine.clock.now_utc(), 'fixed window must have a future end')
            return self.engine.execute(Command(idempotency_key='fixed-start:'+iso_instant(self.engine.clock.now_utc()),
                kind='start_sleep',payload={'duration_minutes':(window_end-self.engine.clock.now_utc()).total_seconds()/60},
                source='legacy_observed',expected_state_version=state.state_version))
        if not sleeping and state.active_sleep_session_id:
            return self.engine.execute(Command(idempotency_key='fixed-end:'+state.active_sleep_session_id,
                kind='wake',payload={'reason':'legacy_window_ended'},source='legacy_observed',expected_state_version=state.state_version))
        return None

    def get_current_activity(self, scope=QueryScope()):
        view=self.queries.get_life_state(scope)
        sleeping=view['sleep_phase']=='sleeping'
        activity=view['activity']
        return {'has_activity':activity is not None,
            'activity':({'name':activity['name'],'description':'','goal_type':activity['category'],
                'time_window':None,'activity_state':'sleeping' if sleeping else 'awake'} if activity else None),
            'activity_state':'sleeping' if sleeping else 'awake','next_activities':[],
            'as_of':iso_instant(self.engine.clock.now_utc()),'timezone':self.timezone,'error':None}
