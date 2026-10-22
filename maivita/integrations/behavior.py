"""Mutually exclusive local/external policy handoff with owned cleanup."""
from ..life.domain.models import require


class BehaviorHandoff:
    def __init__(self, database, owned_frequency, *, character_id='default', owned_streams=()):
        self.database,self.frequency,self.character_id=database,owned_frequency,character_id
        self.owned_streams=set(owned_streams)

    def mode(self):
        with self.database.unit_of_work() as u:
            row=u.connection.execute('SELECT behavior_mode FROM integration_settings WHERE character_id=?',(self.character_id,)).fetchone()
            return row[0] if row else 'local_compat'

    async def switch(self, mode):
        require(mode in ('local_compat','external'), 'unknown integration mode')
        if mode==self.mode(): return True
        # Never mutate global frequency or steal another plugin adjustment.
        for stream in tuple(self.owned_streams):
            if await self.frequency.remove_adjust(stream) is False: return False
            self.owned_streams.discard(stream)
        with self.database.unit_of_work() as u:
            u.connection.execute('INSERT INTO integration_settings(character_id,behavior_mode,revision) VALUES(?,?,1) ON CONFLICT(character_id) DO UPDATE SET behavior_mode=excluded.behavior_mode,revision=revision+1',(self.character_id,mode))
            u.commit()
        return True

    def local_hooks_enabled(self):
        return self.mode() == 'local_compat'
