import json
import pytest
from maicoordinate.plugin import MaiCoordinatePlugin
from maicoordinate.config import Settings


def test_host_webui_has_no_empty_select_and_preserves_default_shadow():
    schema = MaiCoordinatePlugin().get_webui_config_schema(plugin_id='coordinate')
    general = schema['sections']['general']['fields']
    assert general['acknowledgement']['hidden']
    assert general['privileged_writes']['ui_type'] == 'switch'
    assert general['privileged_writes']['disabled']
    config = {}
    for name, section in schema['sections'].items():
        values = {k: v['default'] for k, v in section['fields'].items()}
        if name == 'general': config.update(values)
        else: config[name] = values
        for field in section['fields'].values():
            if field['ui_type'] == 'select': assert '' not in (field.get('choices') or [])
    assert Settings.model_validate(json.loads(json.dumps(config))) == Settings()
    assert Settings().mode == 'shadow'


@pytest.mark.parametrize('patch', [
    {'routes': [{}]}, {'privileged_writes': True}, {'acknowledgement': 'invented'},
])
def test_editor_schema_does_not_relax_bindings(patch):
    MaiCoordinatePlugin.build_config_schema()
    with pytest.raises(ValueError): Settings.model_validate(patch)
