"""Host registers static cards before on_load; read only persisted public facts."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from .life_context import body_feeling


def content(ctx):
    default='加载时暂无生活记录。打开生活页面查看最新活动、心情和睡眠。'
    if ctx is None:return default
    path=ctx.paths.data_dir/'life'/'maivita.db'
    if not path.is_file():return default
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as conn:
            conn.row_factory=sqlite3.Row
            row=conn.execute("SELECT s.sleep_phase,s.updated_at_ms,s.energy,s.fatigue,s.stress,s.sleepiness,a.name,a.emotion "
                "FROM life_state s LEFT JOIN activities a ON a.activity_id=s.primary_activity_id AND a.visibility='public_summary' "
                "WHERE s.character_id='default'").fetchone()
        if row is None:return default
        when=datetime.fromtimestamp(row['updated_at_ms']/1000,timezone.utc).isoformat()
        body={k:row[k]/100 if row[k] is not None else None for k in ('energy','fatigue','stress','sleepiness')}
        return ('加载时快照 · 记录时间 '+when+'\n\n'+('睡眠期间' if row['sleep_phase'] in ('sleeping','temporarily_awake') else '清醒')+
            ' · '+(row['name'] or '暂无公开活动')+' · '+(row['emotion'] or '')+'\n\n'+body_feeling(body)+'\n\n打开生活页面查看最新状态。')
    except (sqlite3.Error,OSError,ValueError):
        return '加载时未能读取生活记录。打开生活页面检查服务状态。'
