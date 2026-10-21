"""Internal identity/policy ports. No claim of an existing host Owner API.

Only an injected, trusted transport adapter creates Identity. User message
fields (including 'owner') are not consulted. Local fallback uses exact IDs.
"""
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_urlsafe
from typing import Protocol
import asyncio

from ..domain.commands import Command
from ..domain.models import require


@dataclass(frozen=True)
class Identity:
    subject_id: str
    stream_id: str
    issuer: str
    verified: bool


class IdentityPort(Protocol):
    async def authenticate(self, trusted_context) -> Identity: ...


@dataclass(frozen=True)
class PolicyRequest:
    action: str
    character_id: str
    stream_id: str
    state_version: int
    config_revision: int


class PolicyPort(Protocol):
    async def allow(self, identity: Identity, request: PolicyRequest) -> bool: ...


class LocalPolicy:
    def __init__(self, admin_ids=(), *, allowed_streams=()):
        self.admin_ids,self.streams = frozenset(admin_ids),frozenset(allowed_streams)

    async def allow(self, identity, request):
        return (identity.verified and identity.subject_id in self.admin_ids
            and identity.stream_id == request.stream_id
            and (not self.streams or request.stream_id in self.streams)
            and request.action in ('wake','interrupt_sleep','configure'))


class WakeAuthorization:
    def __init__(self, engine, identity_port: IdentityPort, policy_port: PolicyPort):
        self.engine,self.identity,self.policy = engine,identity_port,policy_port
        self.permits = {}

    async def authorize(self, trusted_context, *, action, stream_id, character_id='default'):
        require(action in ('wake','interrupt_sleep'), 'unsupported privileged action')
        state = self.engine.state(character_id)
        request = PolicyRequest(action,character_id,stream_id,state.state_version,state.config_revision)
        try:
            identity = await asyncio.wait_for(self.identity.authenticate(trusted_context),5)
            require(type(identity) is Identity and identity.verified and identity.stream_id == stream_id, 'identity denied')
            require(await asyncio.wait_for(self.policy.allow(identity,request),5) is True, 'policy denied')
        except Exception:
            return None  # no permissive fallback on unavailable external policy
        now = self.engine.clock.now_utc()
        self.permits = {token:entry for token,entry in self.permits.items() if entry[1] > now}
        token = token_urlsafe(32)
        self.permits[token] = (request,now+timedelta(seconds=10))
        return token

    def apply(self, token, *, stream_id, action, key, minutes=10):
        entry = self.permits.pop(token,None)
        require(entry is not None, 'unknown/used permit')
        request,expires = entry
        require(request.action == action and request.stream_id == stream_id and self.engine.clock.now_utc() < expires, 'permit scope/expiry mismatch')
        return self.engine.execute(Command(idempotency_key=key,character_id=request.character_id,kind=action,
            payload={'minutes':minutes} if action == 'interrupt_sleep' else {'reason':'authorized_wake'},source='authorized',
            expected_state_version=request.state_version,expected_config_revision=request.config_revision,expires_at=expires))

    def close(self):
        self.permits.clear()
