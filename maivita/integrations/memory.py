"""Optional internal MemoryPort; no assumed host database or API route."""
from typing import Protocol

from ..life.domain.clock import to_epoch_ms
from ..life.domain.models import LifeSummary, require


class MemoryPort(Protocol):
    async def submit_summary(self, *, idempotency_key: str, summary: LifeSummary) -> str: ...


class MemoryAdapter:
    def __init__(self, database, port: MemoryPort, authorize, *, consumer_id):
        self.database,self.port,self.authorize,self.consumer_id=database,port,authorize,consumer_id

    async def submit(self, summary_id, trusted_context):
        with self.database.unit_of_work() as u:
            summary=u.summaries.get(summary_id)
            require(summary is not None, 'unknown summary')
            if u.connection.execute('SELECT 1 FROM memory_deliveries WHERE summary_id=? AND consumer_id=?',(summary_id,self.consumer_id)).fetchone(): return True
        if await self.authorize(trusted_context,summary) is not True: return False
        # Destination must deduplicate this key; uncertain RPC outcomes are
        # reported to caller, not automatically retried in a hidden loop.
        try:
            ack=await self.port.submit_summary(idempotency_key=summary_id,summary=summary)
        except (TimeoutError,ConnectionError): return False
        if ack!=summary_id: return False
        with self.database.unit_of_work() as u:
            u.connection.execute('INSERT OR IGNORE INTO memory_deliveries VALUES(?,?,?)',(summary_id,self.consumer_id,to_epoch_ms(self.database.clock.now_utc())))
            u.commit()
        return True
