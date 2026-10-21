from datetime import timedelta

from ..domain.clock import to_epoch_ms
from ..domain.models import require


class EventDispatcher:
    def __init__(self, database, consumers=None, *, fault=None):
        self.database,self.consumers = database,dict(consumers or {})
        self.fault = fault or (lambda point:None)

    def subscribe(self, consumer_id, handler, prefix='life.'):
        require(consumer_id and prefix.startswith('life.'), 'invalid subscription')
        self.consumers[consumer_id] = handler
        with self.database.unit_of_work() as u:
            u.connection.execute('INSERT INTO event_subscriptions VALUES(?,?,1) ON CONFLICT(consumer_id) DO UPDATE SET event_prefix=excluded.event_prefix,enabled=1',(consumer_id,prefix))
            u.commit()

    async def dispatch(self, *, limit=100):
        require(type(limit) is int and 1 <= limit <= 1000, 'invalid dispatch batch')
        now_ms = to_epoch_ms(self.database.clock.now_utc())
        work=[]
        with self.database.unit_of_work() as u:
            subscriptions=list(u.connection.execute('SELECT consumer_id,event_prefix FROM event_subscriptions WHERE enabled=1'))
            # Keyset-bounded scan: already delivered rows do not grow each tick.
            events=list(u.connection.execute("""SELECT e.* FROM life_events e WHERE dispatch_status='pending'
                AND EXISTS(SELECT 1 FROM event_subscriptions s WHERE s.enabled=1
                AND substr(e.event_type,1,length(s.event_prefix))=s.event_prefix
                AND (NOT EXISTS(SELECT 1 FROM event_deliveries d WHERE d.event_id=e.event_id AND d.consumer_id=s.consumer_id)
                OR EXISTS(SELECT 1 FROM event_deliveries d WHERE d.event_id=e.event_id AND d.consumer_id=s.consumer_id
                   AND d.status='pending' AND (coalesce(d.next_attempt_at_ms,0)<=? OR e.expires_at_ms<=?))))
                ORDER BY character_id,state_version,event_index LIMIT ?""",(now_ms,now_ms,limit)))
            for event in events:
                matching=[row for row in subscriptions if event['event_type'].startswith(row['event_prefix'])]
                if not matching: continue  # no consumer never blocks life state
                for sub in matching:
                    consumer_id=sub['consumer_id']
                    u.connection.execute("INSERT OR IGNORE INTO event_deliveries(event_id,consumer_id,status) VALUES(?,?,'pending')",(event['event_id'],consumer_id))
                    delivery=u.connection.execute('SELECT * FROM event_deliveries WHERE event_id=? AND consumer_id=?',(event['event_id'],consumer_id)).fetchone()
                    if delivery['status'] in ('acknowledged','expired','failed'): continue
                    if event['visibility'] != 'public_summary' or (event['expires_at_ms'] is not None and now_ms >= event['expires_at_ms']):
                        u.connection.execute("UPDATE event_deliveries SET status='expired' WHERE event_id=? AND consumer_id=?",(event['event_id'],consumer_id))
                    elif consumer_id in self.consumers and (delivery['next_attempt_at_ms'] or 0) <= now_ms:
                        work.append((u.events.get(event['event_id']),consumer_id))
            u.commit()
        sent=0
        for event,consumer_id in work:
            with self.database.unit_of_work() as u:
                older=u.connection.execute("""SELECT 1 FROM event_deliveries d JOIN life_events e ON e.event_id=d.event_id
                    WHERE d.consumer_id=? AND d.status='pending' AND e.character_id=?
                    AND (e.state_version<? OR (e.state_version=? AND e.event_index<?)) LIMIT 1""",
                    (consumer_id,event.character_id,event.state_version,event.state_version,event.event_index)).fetchone()
                if older: continue
                row=u.connection.execute('SELECT attempt_count FROM event_deliveries WHERE event_id=? AND consumer_id=?',(event.event_id,consumer_id)).fetchone()
                attempt=row[0]+1
                u.connection.execute('UPDATE event_deliveries SET attempt_count=?,next_attempt_at_ms=? WHERE event_id=? AND consumer_id=?',
                    (attempt,now_ms+min(3600,2**attempt)*1000,event.event_id,consumer_id))
                u.commit()
            sent+=1
            acknowledged=False
            try:
                # Consumer must durably deduplicate event_id. An RPC returning
                # unknown outcome is retried with exactly the same event_id.
                acknowledged=await self.consumers[consumer_id](event) == event.event_id
                self.fault('after_delivery_before_ack')
            except Exception:
                acknowledged=False
            with self.database.unit_of_work() as u:
                if acknowledged:
                    u.connection.execute("UPDATE event_deliveries SET status='acknowledged',acknowledged_at_ms=? WHERE event_id=? AND consumer_id=?",(now_ms,event.event_id,consumer_id))
                elif attempt >= 5:
                    u.connection.execute("UPDATE event_deliveries SET status='failed' WHERE event_id=? AND consumer_id=?",(event.event_id,consumer_id))
                u.commit()
        with self.database.unit_of_work() as u:
            for event in events:
                rows=list(u.connection.execute('SELECT status FROM event_deliveries WHERE event_id=?',(event['event_id'],)))
                if rows and all(row[0] in ('acknowledged','expired') for row in rows):
                    u.connection.execute("UPDATE life_events SET dispatch_status='delivered' WHERE event_id=?",(event['event_id'],))
                elif rows and all(row[0] in ('acknowledged','expired','failed') for row in rows) and any(row[0]=='failed' for row in rows):
                    u.connection.execute("UPDATE life_events SET dispatch_status='failed' WHERE event_id=?",(event['event_id'],))
            u.commit()
        return sent
