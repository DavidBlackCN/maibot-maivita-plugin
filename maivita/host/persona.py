"""Bounded official configuration reads; no secrets, DB or prompt-file access."""
import asyncio,hashlib,json
KEYS=('bot.nickname','personality.personality','personality.behavior_style','personality.reply_style')

async def read_persona(ctx,supplement=''):
    async def get(key):
        try:
            value=await asyncio.wait_for(ctx.config.get(key,''),1)
            return value[:1500] if type(value) is str else ''
        except Exception:return ''
    values=dict(zip(KEYS,await asyncio.gather(*(get(key) for key in KEYS))))
    text=json.dumps({'host':values,'life_supplement':supplement[:4000]},ensure_ascii=False)
    return {'text':text,'digest':hashlib.sha256(text.encode()).hexdigest(),
        'source':'official_config' if any(values.values()) else 'local_supplement_only'}
