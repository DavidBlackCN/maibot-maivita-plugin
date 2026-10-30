"""No-cache scoped projections, separate from authenticated SDK adapters."""
from dataclasses import dataclass

from ..domain.clock import iso_instant,to_epoch_ms
from ..planning.availability import AvailabilityConfig, derive_availability


@dataclass(frozen=True)
class QueryScope:
    stream_id: str | None = None
    private_details: bool = False  # supplied by a trusted policy adapter only


def visible(record, scope):
    source_stream_id = getattr(record, 'source_stream_id', None)
    return record.visibility == 'public_summary' or scope.private_details or (
        record.visibility == 'source_stream'
        and isinstance(source_stream_id, str)
        and bool(source_stream_id.strip())
        and source_stream_id == scope.stream_id)


class LifeQueries:
    def __init__(self, engine, *, availability=None):
        self.engine = engine
        self.availability = availability if availability is not None else AvailabilityConfig()

    def get_public_life_context(self, scope=QueryScope(), character_id='default'):
        """One read transaction, public observations only; plans are never evidence."""
        from datetime import timedelta
        now=self.engine.clock.now_utc()
        cutoff=now-timedelta(hours=24)
        with self.engine.database.read_view() as u:
            state=u.states.get(character_id)
            current=u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
            gaps=list(u.connection.execute('SELECT start_at_ms,end_at_ms FROM observation_gaps WHERE character_id=? AND end_at_ms>?',
                (character_id,to_epoch_ms(cutoff))))
            def unknown(a):
                return any(to_epoch_ms(a.started_at)<right and to_epoch_ms(a.ended_at or now)>left for left,right in gaps)
            def public(a):
                return {'activity_id':a.activity_id,'name':a.name,'category':a.category,
                    'location':a.location,'emotion':a.emotion,'started_at':iso_instant(a.started_at),
                    'ended_at':iso_instant(a.ended_at) if a.ended_at else None,
                    'end_status':a.status.value if hasattr(a.status,'value') else a.status,
                    'continuity_unknown':unknown(a),'visibility':'public_summary',
                    'duration_minutes':None if unknown(a) else max(0,(a.ended_at or now).__sub__(a.started_at).total_seconds()/60)}
            availability=derive_availability(state,current,self.availability,
                activity_visible=current is not None and current.visibility=='public_summary')
            history=sorted((a for a in u.activities.for_character(character_id)
                if a.visibility=='public_summary' and a.role=='primary' and (a.ended_at or now)>cutoff),
                key=lambda a:a.started_at,reverse=True)[:20]
            return {'api_version':1,'as_of':iso_instant(now),'state_version':state.state_version,
                'sleep_phase':state.sleep_phase.value,'awake':state.sleep_phase in ('awake','winding_down') and not state.active_sleep_session_id,
                'availability':{'state':availability.state,'reason':availability.reason},
                'body':{key:getattr(state,key) for key in ('energy','fatigue','stress','confidence')},
                'body_rule':'fixed-categories-v4','activity':public(current) if current and current.visibility=='public_summary' else None,
                'history':[public(a) for a in history], 'continuity_unknown':bool(gaps)}

    def get_life_state(self, scope=QueryScope(), character_id='default'):
        with self.engine.database.read_view() as u:
            state = u.states.get(character_id)
            actual = u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
            unknown=bool(actual and u.connection.execute('SELECT 1 FROM observation_gaps WHERE character_id=? AND end_at_ms>? LIMIT 1',
                (character_id,to_epoch_ms(actual.started_at))).fetchone())
            session=u.sessions.get(state.active_sleep_session_id) if state.active_sleep_session_id else None
            checkpoint=u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=? AND processed=0',(state.primary_activity_id,)).fetchone() if actual else None
            from ..domain.clock import from_epoch_ms
            deadline=(session.interrupt_until or session.expected_wake_at) if session else from_epoch_ms(checkpoint[0]) if checkpoint else None
            return {'character_id':character_id, 'state_version':state.state_version,
                'plan_version':state.plan_version, 'sleep_phase':state.sleep_phase,
                'activity': ({'activity_id':actual.activity_id,'name':actual.name, 'category':actual.category,
                              'started_at':iso_instant(actual.started_at)} if actual and not unknown and visible(actual,scope) else None),
                'activity_continuity_unknown':unknown,
                'energy':state.energy, 'fatigue':state.fatigue, 'stress':state.stress,
                'sleep_debt_minutes':state.sleep_debt_minutes,
                'sleep_history_coverage_days':state.sleep_history_coverage_days,
                'confidence':state.confidence, 'provenance':state.provenance,
                'next_deadline_at':iso_instant(deadline) if deadline else None,
                'degraded_reason':state.degraded_reason,
                'updated_at':iso_instant(state.updated_at)}

    def get_plan(self, scope=QueryScope(), character_id='default'):
        with self.engine.database.read_view() as u:
            state = u.states.get(character_id)
            return {'plan_version':state.plan_version, 'items':[{'item_id':i.item_id, 'name':i.name,
                'time_mode':i.time_mode,'start_at':iso_instant(i.start_at) if i.start_at else None,
                'end_at':iso_instant(i.end_at) if i.end_at else None,
                'window_start_at':iso_instant(i.window_start_at) if i.window_start_at else None,
                'window_end_at':iso_instant(i.window_end_at) if i.window_end_at else None,
                'preferred_duration_minutes':i.preferred_duration_minutes,'status':i.status,'kind':i.kind,'category':i.category}
                for i in u.items.for_character(character_id) if i.plan_id == state.active_plan_id and visible(i,scope)],
                'commitments':[{'commitment_id':c.commitment_id,'title':c.title,'status':c.status}
                for c in u.commitments.for_character(character_id) if visible(c,scope)]}

    def get_goals(self,scope=QueryScope(),character_id='default'):
        with self.engine.database.read_view() as u:
            return [{'goal_id':g.goal_id,'title':g.title,'stage':g.stage,'status':g.status}
                for g in u.goals.for_character(character_id) if visible(g,scope)]

    def get_availability(self, scope=QueryScope(), character_id='default'):
        from ..domain.clock import from_epoch_ms
        with self.engine.database.read_view() as u:
            state = u.states.get(character_id)
            activity = u.activities.get(state.primary_activity_id) if state.primary_activity_id else None
            session = u.sessions.get(state.active_sleep_session_id) if state.active_sleep_session_id else None
            checkpoint = u.connection.execute('SELECT deadline_at_ms FROM activity_checkpoints WHERE activity_id=? AND processed=0',
                (state.primary_activity_id,)).fetchone() if activity else None
            deadline = (session.interrupt_until or session.expected_wake_at) if session else from_epoch_ms(checkpoint[0]) if checkpoint else None
            availability = derive_availability(state, activity, self.availability,
                activity_visible=activity is not None and visible(activity, scope))
        allowed = state.sleep_phase in ('awake','winding_down') and not (
            self.availability.enabled and availability.state == 'busy')
        return {'state_version':state.state_version, 'sleep_phase':state.sleep_phase,
                'state':availability.state, 'reason':availability.reason,
                'enforcement_enabled':self.availability.enabled,
                'available':allowed, 'can_reply_recommendation':allowed,
                'can_proactively_contact_recommendation':allowed,
                'interruptibility':'requires_policy' if state.sleep_phase=='sleeping' else 'policy_dependent',
                'valid_until':iso_instant(deadline) if deadline else None}

    def get_diagnostics(self, scope=QueryScope(), character_id='default'):
        state = self.get_life_state(scope,character_id)
        result = {key:state[key] for key in ('state_version','plan_version','confidence','provenance')}
        result['host_integration'] = 'not_verified'
        result['next_deadline_at']=state['next_deadline_at']
        if scope.private_details:
            with self.engine.database.read_view() as u:
                result['outbox_pending'] = u.connection.execute("SELECT count(*) FROM life_events WHERE character_id=? AND dispatch_status!='delivered'",(character_id,)).fetchone()[0]
                result['decision_status_counts'] = dict(u.connection.execute('SELECT status,count(*) FROM decision_runs WHERE character_id=? GROUP BY status',(character_id,)))
                result['unresolved_import_reviews']=u.connection.execute('SELECT count(*) FROM legacy_import_records WHERE review_reason IS NOT NULL').fetchone()[0]
        return result
