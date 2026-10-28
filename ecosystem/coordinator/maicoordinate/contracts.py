"""Independent strict read adapters. Payload validation is not authentication."""
from datetime import datetime, timezone
import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Unavailable(ValueError):
    pass


def outer(value):
    if type(value) is not dict or value.get('success') is not True or type(value.get('result')) is not dict:
        raise Unavailable('HOST_API_UNAVAILABLE')
    return value['result']


def envelope(value):
    value=outer(value)
    if type(value.get('api_version')) is not int or value['api_version']!=1:
        raise Unavailable('API_VERSION_UNSUPPORTED')
    if value.get('ok') is not True or value.get('error') is not None or type(value.get('data')) is not dict:
        raise Unavailable('DOMAIN_UNAVAILABLE')
    return value['data']


def instant(value):
    if type(value) is not str: raise Unavailable('INVALID_TIME')
    result=datetime.fromisoformat(value)
    if result.utcoffset() is None: raise Unavailable('INVALID_TIME')
    return result.timestamp()


class SocialEvent(BaseModel):
    model_config={'extra':'forbid','strict':True}
    contract_version: Literal['1.0.0']='1.0.0'
    event_id: str=Field(min_length=1,max_length=128)
    character_id: Literal['default']='default'
    event_type: Literal['life.wakeup','life.activity.started','life.activity.ended','life.sleep.started','life.plan.revised']
    state_version: int=Field(ge=0)
    event_index: int=Field(ge=0)
    occurred_at: datetime
    recorded_at: datetime
    expires_at: datetime
    visibility: Literal['public_summary']='public_summary'
    summary: str=Field(max_length=500)

    @model_validator(mode='after')
    def times(self):
        if any(v.utcoffset() is None for v in (self.occurred_at,self.recorded_at,self.expires_at)):
            raise ValueError('explicit timezone required')
        if not self.occurred_at <= self.recorded_at or not self.occurred_at < self.expires_at:
            raise ValueError('invalid event chronology')
        if (self.expires_at-self.occurred_at).total_seconds()>1800:
            raise ValueError('event lifetime exceeds 30 minutes')
        return self

    @classmethod
    def from_wire(cls,value):
        if type(value) is not dict: raise ValueError('event object required')
        return cls.model_validate_json(json.dumps(value,allow_nan=False))


def decide(snapshot, now):
    """Intersection only. An allow here is a proposal, never a send permit."""
    if snapshot.get('error'): return snapshot['error']
    if not now < snapshot['valid_until']: return 'SNAPSHOT_EXPIRED'
    if snapshot['life']['sleep_phase'] not in ('awake','winding_down'): return 'LIFE_UNAVAILABLE'
    if snapshot['hush']['health']!='READY': return 'HUSH_UNAVAILABLE'
    if snapshot['hush']['hard']!='NONE' or snapshot['hush']['soft']!='NORMAL': return 'SPEECH_CONSTRAINED'
    if snapshot['identity'].get('ready') is not True: return 'IDENTITY_UNAVAILABLE'
    return 'ELIGIBLE'
