"""Owned workflow database. It never opens another plugin's database."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


class Store:
    def __init__(self,path,settings):
        self.max_records=settings.max_records
        self.social_events=tuple(settings.social_event_types)
        self.retention_seconds=settings.payload_retention_days*86400
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=self.path.with_suffix('.lock').open('a+b')
        self.conn=None
        try:
            self.lock.seek(0); self.lock.write(b'0'); self.lock.flush(); self.lock.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.conn=sqlite3.connect(self.path)
            self.conn.row_factory=sqlite3.Row
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA synchronous=FULL')
            schema=self.conn.execute('PRAGMA user_version').fetchone()[0]
            tables=self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if schema not in (0,1,2) or schema==0 and tables: raise ValueError('UNSUPPORTED_DATABASE')
            if schema==0:
                self.conn.executescript('''BEGIN;
                    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE actions(producer TEXT,event_id TEXT,route TEXT,payload TEXT,digest TEXT,
                        status TEXT,reason TEXT,basis TEXT,attempt TEXT,updated REAL,
                        PRIMARY KEY(producer,event_id,route));
                    CREATE TABLE cooldown(route TEXT PRIMARY KEY,until_at REAL);
                    PRAGMA user_version=1; COMMIT;''')
            if schema<2:
                self.conn.executescript('''BEGIN;
                    CREATE TABLE IF NOT EXISTS space_actions(operation_id TEXT PRIMARY KEY,kind TEXT NOT NULL,
                        day TEXT NOT NULL,started REAL NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,
                        basis TEXT NOT NULL,result TEXT NOT NULL);
                    PRAGMA user_version=2; COMMIT;''')
            with self.conn:
                self.conn.execute("UPDATE space_actions SET status='unknown',reason='RESTART_DURING_RPC' WHERE status='submitted'")
            binding=canonical([r.model_dump() for r in settings.routes])
            saved=self.get('binding',None)
            if saved is not None and saved!=binding: raise ValueError('OFFLINE_ROUTE_MIGRATION_REQUIRED')
            with self.conn:
                self.put('binding',binding)
                # A crash around generation cannot send; a crash around send
                # has an unknown outcome and must never be retried automatically.
                self.conn.execute("UPDATE actions SET status='cancelled',reason='RESTART_BEFORE_SEND' WHERE status='preparing'")
                self.conn.execute("UPDATE actions SET status='unknown',reason='RESTART_DURING_SEND' WHERE status='executing'")
        except BaseException:
            self.close(); raise

    def get(self,key,default):
        row=self.conn.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self,key,value):
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,canonical(value)))

    def maintain(self,now):
        # Keep compact receipts (including unknown outcomes) indefinitely. Their
        # hashes prevent a producer from reusing an old event ID after cleanup.
        # When the receipt budget fills, stop admission instead of forgetting it.
        with self.conn:
            self.conn.execute("UPDATE actions SET payload='{}' WHERE updated<? AND payload!='{}' AND status NOT IN ('pending','preparing','executing')",
                              (now-self.retention_seconds,))
            self.conn.execute('DELETE FROM cooldown WHERE until_at<=?',(now,))
        self.conn.execute('PRAGMA wal_checkpoint(PASSIVE)')

    def ingest(self,producer,events,cursor,routes,now,capacity):
        with self.conn:
            pending=self.conn.execute("SELECT count(*) FROM actions WHERE status='pending'").fetchone()[0]
            records=self.conn.execute('SELECT count(*) FROM actions').fetchone()[0]
            for event in events:
                payload=canonical(event.model_dump(mode='json')); digest=hashlib.sha256(payload.encode()).hexdigest()
                for route in routes:
                    old=self.conn.execute('SELECT digest FROM actions WHERE producer=? AND event_id=? AND route=?',
                        (producer,event.event_id,route.route_id)).fetchone()
                    if old:
                        if old[0]!=digest: raise ValueError('EVENT_IDEMPOTENCY_CONFLICT')
                        continue
                    records+=1
                    if records>self.max_records: raise ValueError('STORAGE_FULL')
                    status='pending' if event.event_type in self.social_events and now<event.expires_at.timestamp() else 'ignored'
                    if status=='pending':
                        pending+=1
                        if pending>capacity: raise ValueError('QUEUE_FULL')
                    self.conn.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (producer,event.event_id,route.route_id,payload,digest,status,'RECEIVED','{}',None,now))
            self.put('cursor',cursor)  # Atomic with durable receipt; no ACK-before-commit.

    def finish(self,event_id,route,status,reason,now,basis=None,attempt=None):
        with self.conn:
            self.conn.execute('UPDATE actions SET status=?,reason=?,basis=?,attempt=coalesce(?,attempt),updated=? WHERE event_id=? AND route=?',
                (status,reason,canonical(basis or {}),attempt,now,event_id,route))

    def close(self):
        if self.conn is not None: self.conn.close(); self.conn=None
        if self.lock is not None: self.lock.close(); self.lock=None
