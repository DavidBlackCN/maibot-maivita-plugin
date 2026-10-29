"""Frozen v1 wire shapes. Validation does not authenticate an issuer."""
from ..runtime_versions import runtime_descriptor
from datetime import datetime
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = '1.0.0'


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    contract_version: Literal['1.0.0'] = VERSION

    @classmethod
    def from_wire(cls, value):
        """JSON-aware strict parsing; never relax the Python model globally."""
        if type(value) is dict:
            value = json.dumps(value, allow_nan=False)
        if type(value) not in (str, bytes):
            raise ValueError('JSON object or encoded JSON required')
        return cls.model_validate_json(value)

    @model_validator(mode='after')
    def aware_times(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError('contract timestamps require an explicit offset')
        return self


class IdentityRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    context_ref: str = Field(min_length=1, max_length=256)
    action: Literal['read_private', 'wake', 'interrupt_sleep', 'replan', 'configure']
    character_id: Literal['default'] = 'default'
    stream_id: str = Field(min_length=1, max_length=256)


class IdentityDecision(Contract):
    request_id: str
    verified: bool
    subject_ref: str | None
    issuer: str
    stream_id: str
    issued_at: datetime
    expires_at: datetime
    proof_ref: str | None

    @model_validator(mode='after')
    def validate_expiry(self):
        if (self.issued_at.utcoffset() is None or self.expires_at.utcoffset() is None or
            not 0 < (self.expires_at-self.issued_at).total_seconds() <= 30):
            raise ValueError('offset-aware identity lifetime must be 0..30 seconds')
        if self.verified and not (self.subject_ref and self.proof_ref):
            raise ValueError('verified response requires transport-bound principal/proof references')
        return self


class PolicyRequest(IdentityRequest):
    identity_proof_ref: str = Field(min_length=1)
    state_version: int = Field(ge=0)
    config_revision: int = Field(ge=0)


class PolicyDecision(Contract):
    request_id: str
    allowed: bool
    reason_code: str
    state_version: int = Field(ge=0)
    config_revision: int = Field(ge=0)
    expires_at: datetime


class SocialEvent(Contract):
    event_id: str
    character_id: Literal['default'] = 'default'
    event_type: Literal['life.wakeup', 'life.activity.started', 'life.activity.ended', 'life.sleep.started', 'life.plan.revised']
    state_version: int = Field(ge=0)
    event_index: int = Field(ge=0)
    occurred_at: datetime
    recorded_at: datetime
    expires_at: datetime
    visibility: Literal['public_summary'] = 'public_summary'
    summary: str = Field(max_length=500)


class SocialAck(Contract):
    event_id: str
    consumer_id: str
    status: Literal['accepted', 'duplicate', 'expired', 'rejected']


def contract_descriptor():
    return {'contract_version': VERSION, 'status': 'frozen_for_joint_testing',
            **runtime_descriptor(),
            'plugin_version': '0.5.4', 'features':['public_persona_v1','confirmed_life_management_v1','public_events_v1'],
            'local_command_adapter': {'host_patch_required':False,'official_host_support':True,
                'identity_api':'get_vita_policy','commands':['wake','replan','sleep','activity','plan','goal','confirm','cancel','result'],
                'requires':'own official Command envelope and read-only local policy rules',
                'policy_result_is_execution_token':False,'public_mutation_apis':False},
            'public_event_feed': {'api':'get_public_events','version':'1','mode':'read_only_pull','max_batch':100},
            'owner_auth': {'enabled': False, 'reason': 'H03_NO_TRUSTED_IDENTITY_TRANSPORT'},
            'policy': {'enabled': False, 'reason': 'NO_VERIFIED_EXTERNAL_POLICY_TRANSPORT'},
            'social': {'enabled': False, 'reason': 'NO_VERIFIED_CONSUMER_TRANSPORT'},
            'trusted_receipts': {'enabled': False, 'reason': 'H01_UNSUPPORTED'},
            'owned_frequency': {'enabled': False, 'reason': 'H02_UNSUPPORTED'},
            'authorization_from_payload': False, 'production_switch': False}
