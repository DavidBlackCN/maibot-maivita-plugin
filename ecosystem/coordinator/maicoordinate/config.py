"""Operator-only service scope. Nothing in a message can change these bindings."""
import hashlib
import json
from typing import Literal
from maibot_sdk import PluginConfigBase
from pydantic import Field, model_validator

VITA='davidblackcn.maibot-maivita-plugin'
HUSH='davidblackcn.maibot-maihush-plugin'
IDENTITY='davidblackcn.maibot-maiidentity-plugin'
import os
import importlib.metadata
HOST=os.environ.get('MAIBOT_HOST_VERSION','1.3.12')
SDK=importlib.metadata.version('maibot-plugin-sdk')
SUPPORTED_PAIRS={('1.3.12','2.15.0'),('1.3.15','2.16.0'),('1.3.18','2.19.0')}


class Strict(PluginConfigBase):
    model_config={'extra':'forbid','strict':True,'frozen':True}


class PluginSettings(Strict):
    enabled: bool=True
    config_version: Literal['2']='2'


class SpaceSettings(Strict):
    mode: Literal['disabled','shadow','controlled']=Field(default='shadow',description='空间独立模式：关闭、只读观察、自动执行；与群模式分开。')
    timezone: str=Field(default='Asia/Shanghai',description='空间发布和浏览额度的自然日时区。建议与 Vita 相同。')
    post_daily_limit: int=Field(default=2,ge=0,le=2,description='每日发布尝试上限，失败和未知结果也计数。')
    post_interval_seconds: int=Field(default=14400,ge=14400,description='两次发布尝试最小间隔秒数，至少 4 小时。')
    browse_daily_limit: int=Field(default=6,ge=0,le=6,description='每日浏览轮次上限，不是内部模型调用数。')
    browse_interval_seconds: int=Field(default=3600,ge=3600,description='两轮浏览最小间隔秒数，至少 1 小时。')
    max_feeds: int=Field(default=5,ge=1,le=5,description='每轮最多处理的动态条数。')
    interaction_whitelist: list[str]=Field(default_factory=list,max_length=100,description='明确 QQ 号；空名单不互动，须包含在 MaiTrace 的白名单中。')

    @model_validator(mode='after')
    def validate_space(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        if any(not q.isascii() or not q.isdigit() or not 5<=len(q)<=20 for q in self.interaction_whitelist):
            raise ValueError('Space whitelist requires explicit QQ accounts')
        return self


class Route(Strict):
    route_id: str=Field(min_length=1,max_length=128)
    instance_id: str=Field(min_length=1,max_length=128)
    character_id: Literal['default']='default'
    platform: Literal['qq']='qq'
    adapter_namespace: str=Field(min_length=1,max_length=128)
    bot_account_id: str=Field(pattern=r'^[1-9][0-9]{4,19}$')
    route_scope: str=Field(default="",max_length=256)
    group_id: str=Field(pattern=r'^[1-9][0-9]{4,19}$')
    stream_id: str=Field(min_length=1,max_length=256)
    binding_revision: int=Field(default=1,ge=1)

    def hush_fingerprint(self):
        identity=[self.platform,self.adapter_namespace,self.bot_account_id,self.route_scope,self.group_id,self.stream_id]
        return hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()


class Settings(Strict):
    plugin: PluginSettings=Field(default_factory=PluginSettings)
    space: SpaceSettings=Field(default_factory=SpaceSettings)
    mode: Literal['shadow','controlled']='shadow'
    acknowledgement: Literal['','isolated-test-instance']=''
    routes: list[Route]=Field(default_factory=list,max_length=4)
    vita_plugin: Literal[VITA]=VITA
    hush_plugin: Literal[HUSH]=HUSH
    identity_plugin: Literal[IDENTITY]=IDENTITY
    social_event_types: list[Literal['life.wakeup','life.activity.started','life.activity.ended','life.plan.revised']]=Field(default_factory=lambda:['life.wakeup','life.activity.started','life.activity.ended','life.plan.revised'],max_length=4)
    model_task: str=Field(default='utils',max_length=128)
    character_context: str=Field(default='',max_length=2000)
    poll_seconds: int=Field(default=5,ge=1,le=60)
    cooldown_seconds: int=Field(default=3600,ge=60,le=86400)
    queue_capacity: int=Field(default=256,ge=1,le=1024)
    max_records: int=Field(default=10000,ge=256,le=100000)
    payload_retention_days: int=Field(default=7,ge=1,le=90)
    privileged_writes: Literal[False]=False

    @model_validator(mode='before')
    @classmethod
    def migrate_space(cls,data):
        if isinstance(data,dict) and str(data.get('plugin',{}).get('config_version','2'))=='1':
            from copy import deepcopy
            data=deepcopy(data)
            data.setdefault('plugin',{})['config_version']='2'
            data['space']={'mode':'disabled'}
        return data

    @model_validator(mode='after')
    def scope(self):
        if any(r.route_scope != r.route_scope.strip() or '*' in r.route_scope for r in self.routes):
            raise ValueError('route scope must be exact; empty means Host scope=None')
        if self.max_records < self.queue_capacity:
            raise ValueError('record budget must fit the pending queue')
        if any(not value.strip() for r in self.routes for value in
               (r.route_id,r.instance_id,r.adapter_namespace,r.stream_id)):
            raise ValueError('blank route bindings are forbidden')
        if self.queue_capacity < len(self.routes):
            raise ValueError('queue capacity must fit one event across configured routes')
        if self.mode=='controlled' and not self.model_task.strip():
            raise ValueError('controlled mode requires an existing Host model task')
        if len({r.route_id for r in self.routes})!=len(self.routes) or len({r.stream_id for r in self.routes})!=len(self.routes):
            raise ValueError('duplicate route/stream binding')
        return self
