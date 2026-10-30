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
    assert 'acknowledgement' not in schema['sections']['instance']['fields']
    assert not {'owner_auth_enabled', 'privileged_writes_enabled'} & schema['sections']['instance']['fields'].keys()
    assert schema['sections']['plugin']['fields']['config_version']['disabled']
    assert schema['sections']['model']['fields']['timeout_seconds']['hidden']
    assert schema['sections']['scheduler']['collapsed']
    assert schema['sections']['availability']['collapsed']
    assert not schema['sections']['model']['fields']['daily_budget']['hidden']
    config = {name: {k: v['default'] for k, v in s['fields'].items()} for name, s in schema['sections'].items()}
    assert IntegrationConfig.model_validate(json.loads(json.dumps(config))) == IntegrationConfig()


@pytest.mark.parametrize('patch', [
    {'instance': {'acknowledgement': 'invented'}},
    {'instance': {'unknown_key': 1}},
])
def test_webui_presentation_does_not_relax_runtime(patch):
    MaiVitaPlugin.build_config_schema()
    with pytest.raises(ValueError):
        IntegrationConfig.model_validate(patch)


@pytest.mark.parametrize('patch', [
    {'instance': {'owner_auth_enabled': True}}, {'instance': {'privileged_writes_enabled': False}},
    {'routine': {'weekly_pattern': {}}}, {'scheduler': {'daily_decision_budget': 20}},
    {'scheduler': {'change_chance_percent': 0}}, {'scheduler': {'enabled': True}},
    {'availability': {'followup_after_busy': False}}, {'plugin': {'config_version': '3'}},
    {'engine': {}}, {'legacy': {}},
])
def test_only_current_configuration_is_accepted(patch):
    with pytest.raises(ValueError):
        IntegrationConfig.model_validate(patch)
