"""SDK lifecycle entrypoint for the life engine."""
from dataclasses import replace

from maibot_sdk import API, MaiBotPlugin
from .activation import EngineAuthority
from .config import stream_allowed
from .integration_config import IntegrationConfig
from .plugin import MaiVitaDevelopmentPlugin
from ..life.storage.safety import resolve_data_dir
from ..runtime_versions import runtime_pair, runtime_supported


class EngineContext:
    """Scope granted paths; capabilities remain the original SDK proxies."""
    def __init__(self, context):
        self.context = context
        self.paths = replace(context.paths, data_dir=context.paths.data_dir / 'life',
                             runtime_dir=context.paths.runtime_dir / 'life')

    def __getattr__(self, name):
        return getattr(self.context, name)


class MaiVitaPlugin(MaiBotPlugin):
    config_model = IntegrationConfig

    @classmethod
    def build_config_schema(cls, **metadata):
        from .schema import build_schema
        return build_schema(**metadata)

    def __init__(self):
        super().__init__()
        self.delegate = None
        self.active_config = None
        self.authority = None
        self.ready = False
        self.failure_code = None
        self.handler_types = {}
        self.component_names = {}

    def normalize_plugin_config(self, config_data):
        if config_data:
            IntegrationConfig.model_validate(config_data)
        return super().normalize_plugin_config(config_data)

    def _select(self):
        if self._plugin_config_instance is None:
            self.set_plugin_config({})
        if self.delegate is None:
            self.delegate = MaiVitaDevelopmentPlugin()
        if self.active_config is None:
            # SDK metadata discovery can happen before the actual config is
            # injected. Never activate a delegate using its earlier defaults.
            self.delegate.set_plugin_config(self.config.selected_config().model_dump())
        return self.delegate

    def get_components(self):
        delegate = self._select()
        own = super().get_components()
        own_names = {component['name'] for component in own}
        components = [component for component in delegate.get_components() if component['name'] not in own_names]
        self.handler_types = {c['metadata'].get('handler_name', c['name']): c['type'] for c in components}
        self.component_names = {c['metadata'].get('handler_name', c['name']): c['name'] for c in components}
        return components + own

    def get_config_reload_subscriptions(self):
        return self._select().get_config_reload_subscriptions()

    def __getattr__(self, name):
        types = self.__dict__.get('handler_types', {})
        if name not in types:
            raise AttributeError(name)
        async def invoke(**kwargs):
            if not self.ready:
                if types[name] == 'HOOK_HANDLER':
                    if 'sleep' not in self.component_names[name]:
                        return {'action': 'continue'}
                    message = kwargs.get('message')
                    stream = message.get('session_id') if isinstance(message, dict) else getattr(message, 'session_id', None)
                    return ({'action': 'abort', 'reason': 'maivita_not_ready'} if self.config.plugin.enabled and (not stream or
                            stream_allowed(stream, self.config.instance.allowed_streams)) else {'action': 'continue'})
                if types[name] == 'COMMAND':
                    return True, 'MaiVita not ready; reload required', 2
                if self.component_names[name].endswith('get_current_activity'):
                    return {'has_activity': False, 'activity': None, 'error': self.failure_code or 'NOT_READY'}
                return {'api_version': 1, 'ok': False, 'data': None,
                        'error': {'code': self.failure_code or 'NOT_READY', 'retryable': False},
                        'state_version': None, 'as_of': None}
            return await getattr(self.delegate, name)(**kwargs)
        return invoke

    async def on_load(self):
        if self.ready or self.active_config is not None:
            raise RuntimeError('engine lifecycle already active')
        if not self.config.plugin.enabled:
            return
        try:
            if not runtime_supported():
                raise RuntimeError('unsupported Host/SDK pair: '+repr(runtime_pair()))
            data = resolve_data_dir(self.ctx)
            if (data / 'goals.db').exists() or (data / 'maivita.db').exists():
                raise ValueError('unscoped database requires explicit offline migration')
            authority = EngineAuthority(data)
            resolved = self.config.resolved(data)
            authority.check(resolved)
            delegate = self._select()
            delegate.set_plugin_config(resolved.selected_config().model_dump())
            delegate._set_context(EngineContext(self.ctx))
            await delegate.on_load()
            lease = delegate.deployment_lease
            if not delegate.ready or lease is None or lease.file is None:
                raise RuntimeError('selected delegate did not become ready and acquire its writer lease')
            # Recheck and persist only after the delegate owns the common lock.
            self.authority = authority.activate(resolved)
            self.active_config = self.config.model_copy(deep=True)
            self.ready = True
            self.failure_code = None
        except BaseException:
            self.failure_code = 'ACTIVATION_FAILED'
            await self.on_unload()
            raise

    async def on_unload(self):
        self.ready = False
        try:
            if self.delegate is not None:
                await self.delegate.on_unload()
        finally:
            self.active_config = None

    async def on_config_update(self, scope, config_data, version):
        if scope != 'self':
            if self.ready:
                await self.delegate.on_config_update(scope, config_data, version)
            return
        if self.active_config is not None and config_data == self.active_config.model_dump():
            return
        # Even invalid updates stop the current writer. Nothing starts a second
        # delegate in this process; Host reload re-registers the life components.
        await self.on_unload()
        self.failure_code = 'RELOAD_REQUIRED'
        IntegrationConfig.model_validate(config_data)
        raise RuntimeError('configuration changed; offline transition and/or Host reload required')

    @API('get_engine_status', public=True, version='1')
    async def get_engine_status(self, **kwargs):
        live = self.ready and self.delegate.ready
        public_authority = ({key: self.authority[key] for key in ('engine', 'revision', 'database')}
                            if self.authority else None)
        return {'api_version': 1, 'ready': live, 'engine': 'life',
                'authority': public_authority, 'failure_code': self.failure_code or getattr(self.delegate, 'failure_code', None),
                'production_switch': False}

    @API('get_integration_contract', public=True, version='1')
    async def get_integration_contract(self, **kwargs):
        from ..integrations.contracts import contract_descriptor
        return contract_descriptor()

    @staticmethod
    def unavailable(code):
        return {'api_version': 1, 'ok': False, 'data': None,
                'error': {'code': code, 'message': code, 'retryable': False},
                'state_version': None, 'as_of': None}

    async def life_query(self, method):
        if not self.ready:
            return self.unavailable(self.failure_code or 'NOT_READY')
        return await self.delegate.read(method)

    # Stable static bindings are required by the native WebUI validator.
    @API('get_life_state', public=True, version='1')
    async def get_life_state(self, **kwargs): return await self.life_query('get_life_state')
    @API('get_plan', public=True, version='1')
    async def get_plan(self, **kwargs): return await self.life_query('get_plan')
    @API('get_availability', public=True, version='1')
    async def get_availability(self, **kwargs): return await self.life_query('get_availability')
    @API('get_diagnostics', public=True, version='1')
    async def get_diagnostics(self, **kwargs): return await self.life_query('get_diagnostics')
    @API('get_public_events', public=True, version='1')
    async def get_public_events(self, after_version=0, after_index=-1, limit=50, **kwargs):
        if not self.ready: return self.unavailable('NOT_READY')
        if not self.delegate.ready: return self.unavailable('NOT_READY')
        from ..integrations.social import public_events
        from ..life.domain.models import DomainError
        try:
            data=public_events(self.delegate.runtime.database,after_version,after_index,limit)
            return {'api_version':1,'ok':True,'data':data,'error':None}
        except (DomainError,ValueError,TypeError): return self.unavailable('INVALID_ARGUMENT')

    @API('get_public_persona', public=True, version='1')
    async def get_public_persona(self, **kwargs):
        if not self.ready or not self.delegate.ready:return self.unavailable('NOT_READY')
        delegate=self.delegate;cfg=delegate.active_config
        from .persona import read_persona
        data=await read_persona(delegate.ctx,cfg.model.life_context)
        if not self.ready or self.delegate is not delegate or delegate.active_config is not cfg:return self.unavailable('NOT_READY')
        return {'api_version':1,'ok':True,'data':data,'error':None}

    @API('get_public_route_scope', public=True, version='1')
    async def get_public_route_scope(self, stream_id=None, **kwargs):
        if not self.ready or not self.delegate.ready:
            return self.unavailable('ENGINE_UNAVAILABLE')
        if not stream_allowed(stream_id, self.active_config.instance.allowed_streams):
            return self.unavailable('ROUTE_UNAVAILABLE')
        return {'api_version':1,'ok':True,'data':{'stream_id':stream_id,'character_id':'default','guarded':True,
            'config_digest':self.authority['config_sha256']},'error':None}
    @API('request_replan', public=True, version='1')
    async def request_replan(self, **kwargs): return self.unavailable('PERMISSION_DENIED')
    @API('request_wakeup', public=True, version='1')
    async def request_wakeup(self, **kwargs): return self.unavailable('POLICY_UNAVAILABLE')


def create_plugin():
    return MaiVitaPlugin()
