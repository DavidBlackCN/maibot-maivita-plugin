"""Bounded pull -> durable receipt -> decision -> generation -> recheck -> send."""
import asyncio
import json
import time
import uuid
from .config import VITA, HUSH, IDENTITY, HOST, SDK
from .contracts import Unavailable, SocialEvent, outer, envelope, instant, decide


class Coordinator:
    def __init__(self,ctx,settings,store,*,wall=time.time,mono=time.monotonic):
        self.ctx,self.settings,self.store=ctx,settings,store
        self.wall,self.mono=wall,mono
        self.health='STARTING'; self.last_error=None
        self.gate=asyncio.Lock()
        self.last_wall=store.get('last_wall',0)

    async def call(self,plugin,method,**args):
        return await asyncio.wait_for(self.ctx.api.call(plugin+'.'+method,version='1',**args),2)

    async def discover(self):
        for plugin,methods in ((VITA,('get_engine_status','get_life_state','get_public_events','get_public_route_scope','get_integration_contract','get_public_persona')),
                               (HUSH,('get_contract','get_public_constraints')),(IDENTITY,('get_contract','get_public_social_context'))):
            value=await asyncio.wait_for(self.ctx.api.list(plugin_id=plugin),2)
            if type(value) is not dict or value.get('success') is not True or type(value.get('apis')) is not list:
                raise Unavailable('API_DISCOVERY_FAILED')
            entries=value['apis']
            for method in methods:
                if not any(type(e) is dict and e.get('plugin_id')==plugin and e.get('name')==method and str(e.get('version'))=='1' for e in entries):
                    raise Unavailable('API_MISSING:'+method)
        vita,hush,identity=await asyncio.gather(self.call(VITA,'get_integration_contract'),
            self.call(HUSH,'get_contract'),self.call(IDENTITY,'get_contract'))
        vita,hush,identity=outer(vita),envelope(hush),envelope(identity)
        if vita.get('contract_version')!='1.0.0' or hush.get('contract_version')!='1.1.0' or identity.get('api_major')!='1':
            raise Unavailable('CONTRACT_VERSION_UNSUPPORTED')
        for value in (vita,hush,identity):
            if (value.get('host'),value.get('sdk'))!=(HOST,SDK): raise Unavailable('BASELINE_MISMATCH')
        if not {'public_persona_v1','public_events_v1'}<=set(vita.get('features',[])):raise Unavailable('VITA_FEATURE_UNSUPPORTED')
        if identity.get('plugin_id')!=IDENTITY: raise Unavailable('IDENTITY_PLUGIN_MISMATCH')
        if 'public_social_context_v1' not in identity.get('features',[]):raise Unavailable('IDENTITY_FEATURE_UNSUPPORTED')

    async def snapshot(self,route):
        start=self.wall(); started=self.mono()
        try:
            if start < self.last_wall: raise Unavailable('CLOCK_ROLLBACK')
            await self.discover()
            engine,life,hush,identity,scope,persona,social=await asyncio.gather(
                self.call(VITA,'get_engine_status'),self.call(VITA,'get_life_state'),
                self.call(HUSH,'get_public_constraints',stream_id=route.stream_id),
                self.call(IDENTITY,'get_contract'),self.call(VITA,'get_public_route_scope',stream_id=route.stream_id),
                self.call(VITA,'get_public_persona'),self.call(IDENTITY,'get_public_social_context',stream_id=route.stream_id,bot_account_id=route.bot_account_id,route_scope=route.route_scope or None))
            persona,social=envelope(persona),envelope(social)
            if (type(persona.get('text')) is not str or len(persona['text'])>15000 or type(persona.get('digest')) is not str or len(persona['digest'])!=64
                or type(social.get('revision')) is not str or len(social['revision'])!=64 or social.get('stream_id')!=route.stream_id):raise Unavailable('PUBLIC_CONTEXT_INVALID')
            if (set(social)!={'stream_id','counts','relation_state','completeness','style','rule','revision'}
                or type(social['counts']) is not dict or set(social['counts'])!={'friend','family','partner'}
                or any(type(n) is not int or not 0<=n<=20 for n in social['counts'].values())
                or social['relation_state'] not in ('none_confirmed','confirmed','disputed')
                or social['style'] not in ('neutral','friendly') or social['completeness']!='public_group_scope_only'
                or type(social['rule']) is not str or len(social['rule'])>500):raise Unavailable('PUBLIC_CONTEXT_INVALID')
            engine=outer(engine); life_envelope=outer(life)
            life,hush,identity,scope=envelope(life),envelope(hush),envelope(identity),envelope(scope)
            if engine.get('ready') is not True or engine.get('engine')!='life': raise Unavailable('LIFE_ENGINE_UNAVAILABLE')
            if (scope.get('stream_id')!=route.stream_id or scope.get('character_id')!=route.character_id or scope.get('guarded') is not True
                or type(scope.get('config_digest')) is not str or len(scope['config_digest'])!=64): raise Unavailable('VITA_ROUTE_MISMATCH')
            if hush.get('stream_id')!=route.stream_id or hush.get('route_fingerprint')!=route.hush_fingerprint():
                raise Unavailable('HUSH_ROUTE_MISMATCH')
            if identity.get('plugin_id')!=IDENTITY or identity.get('api_major')!='1': raise Unavailable('IDENTITY_CONTRACT_MISMATCH')
            if life.get('character_id')!=route.character_id: raise Unavailable('CHARACTER_MISMATCH')
            if life.get('sleep_phase') not in ('awake','winding_down','sleeping','temporarily_awake','waking'):
                raise Unavailable('LIFE_STATE_UNKNOWN')
            if hush.get('health') not in ('READY','DEGRADED') or hush.get('hard') not in ('NONE','HARD_SILENT') or hush.get('soft') not in ('NORMAL','RESTRAINED','SOFT_SILENT','RECOVERING'):
                raise Unavailable('HUSH_STATE_UNKNOWN')
            for value in (life.get('state_version'),life.get('plan_version'),hush.get('state_version'),hush.get('config_revision')):
                if type(value) is not int or value<0: raise Unavailable('INVALID_VERSION')
            for value in (hush.get('as_of'),hush.get('valid_until')):
                if type(value) not in (int,float) or not 0<value<float('inf'): raise Unavailable('INVALID_TIME')
            now=self.wall()
            as_of=instant(life_envelope.get('as_of'))
            if not start-5<=as_of<=now+1 or not start-5<=hush['as_of']<=now+1:
                raise Unavailable('STALE_SNAPSHOT')
            if now<start or self.mono()-started>5: raise Unavailable('CLOCK_OR_READ_TIMEOUT')
            valid=min(start+5,as_of+5,hush['valid_until'])
            if life.get('next_deadline_at'):
                valid=min(valid,instant(life['next_deadline_at']))
            basis={'life':life['state_version'],'plan':life['plan_version'],
                   'hush':hush['state_version'],'hush_config':hush['config_revision'],'route':route.binding_revision,
                   'vita_config':scope['config_digest'],'persona':persona['digest'],'social':social['revision']}
            return {'life':life,'hush':hush,'identity':identity,'persona':persona,'social':social,'basis':basis,'valid_until':valid,'error':None}
        except Exception as exc:
            return {'error':str(exc) if isinstance(exc,Unavailable) else 'DEPENDENCY_UNAVAILABLE','valid_until':0}

    async def ingest(self):
        cursor=self.store.get('cursor',[0,-1])
        limit=min(50,max(1,self.settings.queue_capacity//max(1,len(self.settings.routes))))
        data=envelope(await self.call(VITA,'get_public_events',after_version=cursor[0],after_index=cursor[1],limit=limit))
        new=data.get('cursor')
        if type(new) is not list or len(new)!=2 or any(type(x) is not int for x in new) or tuple(new)<tuple(cursor):
            raise Unavailable('INVALID_CURSOR')
        values=data.get('events')
        if type(values) is not list or len(values)>limit: raise Unavailable('INVALID_EVENT_BATCH')
        now=self.wall(); events=[SocialEvent.from_wire(v) for v in values]
        previous=tuple(cursor)
        for event in events:
            position=(event.state_version,event.event_index)
            if not previous<position<=tuple(new) or event.recorded_at.timestamp()>now+1:
                raise Unavailable('INVALID_EVENT_ORDER_OR_TIME')
            previous=position
        self.store.ingest(VITA,events,new,self.settings.routes,now,self.settings.queue_capacity)

    async def process_route(self,route):
        rows=self.store.conn.execute("SELECT * FROM actions WHERE route=? AND status='pending' ORDER BY updated,event_id",(route.route_id,)).fetchall()
        if not rows: return
        # One current opportunity per route. Never replay a quiet-period backlog.
        rows=sorted(rows,key=lambda r:(json.loads(r['payload'])['state_version'],json.loads(r['payload'])['event_index']))
        row=rows[-1]
        for older in rows[:-1]: self.store.finish(older['event_id'],route.route_id,'cancelled','SUPERSEDED',self.wall())
        event=SocialEvent.from_wire(json.loads(row['payload'])); event_id=event.event_id
        def finish(status,reason,basis=None,attempt=None):
            self.store.finish(event_id,route.route_id,status,reason,self.wall(),basis,attempt)
        if event.occurred_at.timestamp()<self.store.get('route_since:'+route.route_id,0):
            finish('ignored','BEFORE_ROUTE_ADMISSION'); return
        if self.wall()>=event.expires_at.timestamp(): finish('expired','EVENT_EXPIRED'); return
        snapshot=await self.snapshot(route)
        reason=decide(snapshot,self.wall())
        basis=snapshot.get('basis',{})
        if reason!='ELIGIBLE': finish('cancelled',reason,basis); return
        if snapshot['life']['state_version']<event.state_version: finish('cancelled','EVENT_AHEAD_OF_STATE',basis); return
        cooldown=self.store.conn.execute('SELECT until_at FROM cooldown WHERE route=?',(route.route_id,)).fetchone()
        if cooldown and self.wall()<cooldown[0]: finish('cancelled','COOLDOWN',basis); return
        if self.settings.mode=='shadow': finish('shadow','WOULD_GENERATE_AND_RECHECK',basis); return
        # Bound model spending even when generation fails or the state changes.
        # A failed/ambiguous attempt never refunds this persistent reservation.
        with self.store.conn:
            self.store.conn.execute('INSERT OR REPLACE INTO cooldown VALUES(?,?)',
                (route.route_id,self.wall()+self.settings.cooldown_seconds))
        finish('preparing','GENERATING',basis)
        try:
            # Fixed intent; no untrusted event payload or private records in prompt.
            from .expression import prompt as expression_prompt
            prompt=expression_prompt(event,snapshot,self.store,route.route_id,self.settings.character_context)
            response=await asyncio.wait_for(self.ctx.llm.generate(prompt,task_name=self.settings.model_task,max_tokens=128,rpc_timeout_ms=8000),8)
            text=response.get('response') if type(response) is dict and response.get('success') is True else None
            if type(text) is not str or not text.strip() or len(text)>200:
                finish('failed','GENERATION_INVALID',basis); return
            current=await self.snapshot(route)
            reason=decide(current,self.wall())
            if self.wall()>=event.expires_at.timestamp(): finish('expired','EVENT_EXPIRED',basis); return
            if reason!='ELIGIBLE' or current.get('basis')!=basis:
                finish('cancelled',reason if reason!='ELIGIBLE' else 'STATE_CHANGED',current.get('basis',{})); return
            attempt=uuid.uuid4().hex
            # Commit before external effect. Reserve cooldown even on unknown
            # outcome; neither a timeout nor a restart can refund it.
            with self.store.conn:
                self.store.conn.execute('INSERT OR REPLACE INTO cooldown VALUES(?,?)',(route.route_id,self.wall()+self.settings.cooldown_seconds))
                self.store.conn.execute("UPDATE actions SET status='executing',reason='SEND_ATTEMPT',attempt=?,updated=? WHERE event_id=? AND route=?",(attempt,self.wall(),event_id,route.route_id))
            result=await asyncio.wait_for(self.ctx.send.text(text.strip(),route.stream_id,return_details=True),10)
            if type(result) is dict and result.get('success') is True and result.get('sent') is True:
                finish('sent_some','HOST_CONFIRMED_AT_LEAST_ONE_DELIVERY',basis)
            elif type(result) is dict and result.get('success') is False and result.get('sent') is False:
                finish('not_sent','HOST_DID_NOT_CONFIRM_DELIVERY',basis)
            else: finish('unknown','UNRECOGNIZED_SEND_RESULT',basis)
        except BaseException as exc:
            status=self.store.conn.execute('SELECT status FROM actions WHERE event_id=? AND route=?',(event_id,route.route_id)).fetchone()[0]
            finish('unknown' if status=='executing' else 'failed','SEND_OUTCOME_UNKNOWN' if status=='executing' else 'GENERATION_UNAVAILABLE',basis)
            if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)): raise

    async def cycle(self):
        async with self.gate:
            now=self.wall()
            if now < self.last_wall: raise Unavailable('CLOCK_ROLLBACK')
            self.last_wall=now
            with self.store.conn: self.store.put('last_wall',now)
            self.store.maintain(now)
            await self.discover()
            admission_error=None
            try: await self.ingest()
            except ValueError as exc:
                if str(exc) not in ('QUEUE_FULL','STORAGE_FULL'): raise
                admission_error=str(exc)
                # Keep the old cursor and drain/merge pending work. A full
                # inbox must not prevent its own processing indefinitely.
            # Structured lifetime: on failure/cancellation all sibling tasks
            # are cancelled AND joined before the owner can close SQLite.
            async with asyncio.TaskGroup() as group:
                for route in self.settings.routes: group.create_task(self.process_route(route))
            self.health='DEGRADED' if admission_error else 'READY'
            self.last_error=admission_error
