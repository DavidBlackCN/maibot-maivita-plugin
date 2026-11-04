"""Bounded fictional reflections on observed activities; never a memory writer."""
import json
from datetime import timedelta

from ..domain.clock import to_epoch_ms


def record(u, current, reflection, now):
    if not current or not reflection or current.sleep_session_id or now-current.started_at < timedelta(minutes=15):
        return
    start, end=to_epoch_ms(current.started_at),to_epoch_ms(now)
    if u.connection.execute('SELECT 1 FROM observation_gaps WHERE character_id=? AND start_at_ms<? AND end_at_ms>?',
                            (current.character_id,end,start)).fetchone():
        return
    details=[s.strip() for s in reflection.get('details',[])]
    next_step=reflection.get('next_step','').strip()
    # Once recorded, reuse across all chats; a later model cannot rewrite it.
    u.connection.execute('INSERT OR IGNORE INTO activity_details VALUES(?,?,?,?,?,?)',
        (current.activity_id,json.dumps(details,ensure_ascii=False),next_step,'character_simulation',
         'active' if next_step else 'ended',end))
    expire(u,now)


def expire(u,now):
    u.connection.execute("UPDATE activity_details SET status='archived' WHERE status='active' AND updated_at_ms<?",
                         (to_epoch_ms(now-timedelta(days=7)),))
    rows=u.connection.execute("SELECT activity_id FROM activity_details WHERE status='active' ORDER BY updated_at_ms DESC,activity_id DESC").fetchall()
    for row in rows[5:]:
        u.connection.execute("UPDATE activity_details SET status='archived' WHERE activity_id=?",(row[0],))


def read(u,activity_id):
    row=u.connection.execute('SELECT * FROM activity_details WHERE activity_id=?',(activity_id,)).fetchone()
    return {'details':json.loads(row['details_json']),'next_step':row['next_step'],'source':row['source'],
            'status':row['status']} if row else {'details':[],'next_step':'','source':'character_simulation','status':'none'}


def continuing(u,now):
    rows=u.connection.execute("SELECT d.*,a.name,a.visibility FROM activity_details d JOIN activities a USING(activity_id) "
        "WHERE d.status='active' AND a.visibility='public_summary' AND d.updated_at_ms>=? "
        "ORDER BY d.updated_at_ms DESC,d.activity_id DESC LIMIT 5",(to_epoch_ms(now-timedelta(days=7)),)).fetchall()
    return [{'activity_id':r['activity_id'],'name':r['name'],'details':json.loads(r['details_json']),
             'next_step':r['next_step'],'source':r['source'],'updated_at_ms':r['updated_at_ms']} for r in rows]
