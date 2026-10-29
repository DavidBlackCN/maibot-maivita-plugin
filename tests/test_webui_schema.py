import json
import pytest
import r0_support
from maibot_autonomous_planning_plugin.maivita.host.entrypoint import MaiVitaPlugin
from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig


def test_host_webui_schema_has_no_empty_select_and_roundtrips_defaults():
    schema = MaiVitaPlugin().get_webui_config_schema(plugin_id='vita', plugin_version='test')
    assert schema['plugin_id'] == 'vita'
    assert 'engine' not in schema['sections']
    assert 'legacy' not in schema['sections']
    fields = [f for s in schema['sections'].values() for f in s['fields'].values()]
    assert all('' not in (f.get('choices') or []) for f in fields if f['ui_type'] == 'select')
    assert schema['sections']['instance']['fields']['acknowledgement']['hidden']
    assert schema['sections']['instance']['fields']['privileged_writes_enabled']['ui_type'] == 'switch'
    assert schema['sections']['plugin']['fields']['config_version']['disabled']
    config = {name: {k: v['default'] for k, v in s['fields'].items()} for name, s in schema['sections'].items()}
    assert IntegrationConfig.model_validate(json.loads(json.dumps(config))) == IntegrationConfig()


@pytest.mark.parametrize('patch', [
    {'instance': {'acknowledgement': 'invented'}},
    {'instance': {'privileged_writes_enabled': True}},
    {'instance': {'owner_auth_enabled': True}},
])
def test_webui_presentation_does_not_relax_runtime(patch):
    MaiVitaPlugin.build_config_schema()
    with pytest.raises(ValueError):
        IntegrationConfig.model_validate(patch)
