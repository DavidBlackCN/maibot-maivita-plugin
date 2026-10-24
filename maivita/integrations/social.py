"""Public event projection and explicit v1 JSON/ack boundary.

The pull API is read-only; caller-supplied producer IDs never authenticate events.
"""
from .contracts import SocialEvent, SocialAck
from ..life.events.dispatcher import DeliveryResult
from ..life.domain.models import require
from ..life.domain.clock import to_epoch_ms

SUMMARIES = {
    'life.wakeup': 'The character has woken up.',
    'life.activity.started': 'A life activity has started.',
    'life.activity.ended': 'A life activity has ended.',
    'life.sleep.started': 'The character has fallen asleep.',
    'life.plan.revised': 'The life plan has changed.',
}


def public_event(event):
    require(event.visibility == 'public_summary' and event.event_type in SUMMARIES,
            'event is not an allowed public social event')
    require(event.expires_at is not None, 'event requires expiry')
    return SocialEvent(event_id=event.event_id, character_id=event.character_id,
        event_type=event.event_type, state_version=event.state_version, event_index=event.event_index,
        occurred_at=event.occurred_at, recorded_at=event.recorded_at, expires_at=event.expires_at,
        summary=SUMMARIES[event.event_type])


class SocialConsumer:
    """An injected transport must already be bound to an operator-chosen peer."""
    def __init__(self, consumer_id, call):
        self.consumer_id, self.call = consumer_id, call

    async def __call__(self, event):
        if event.event_type not in SUMMARIES or event.visibility != 'public_summary':
            return DeliveryResult(event.event_id, 'rejected')
        ack = SocialAck.from_wire(await self.call(public_event(event).model_dump(mode='json')))
        require(ack.event_id == event.event_id and ack.consumer_id == self.consumer_id, 'ack binding mismatch')
        return DeliveryResult(event.event_id, ack.status)


def public_events(database, after_version=0, after_index=-1, limit=50):
    require(type(after_version) is int and after_version >= 0 and type(after_index) is int and after_index >= -1,
            'invalid event cursor')
    require(type(limit) is int and 1 <= limit <= 100, 'invalid event batch')
    now=database.clock.now_utc()
    with database.read_view() as u:
        rows=list(u.connection.execute("""SELECT event_id FROM life_events
            WHERE character_id='default' AND visibility='public_summary'
            AND (state_version>? OR (state_version=? AND event_index>?))
            ORDER BY state_version,event_index LIMIT ?""",(after_version,after_version,after_index,limit)))
        events=[]; cursor=[after_version,after_index]
        for row in rows:
            event=u.events.get(row[0]); cursor=[event.state_version,event.event_index]
            if event.event_type in SUMMARIES and event.expires_at and event.expires_at>now:
                events.append(public_event(event).model_dump(mode='json'))
    return {'events':events,'cursor':cursor,'as_of':now.isoformat()}
