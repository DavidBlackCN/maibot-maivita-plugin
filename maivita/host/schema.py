"""WebUI presentation only; the runtime configuration stays authoritative."""
from maibot_sdk.config import generate_plugin_config_schema
from .integration_config import IntegrationConfig


def build_schema(**metadata):
    schema = generate_plugin_config_schema(IntegrationConfig, **metadata)
    for section in schema['sections'].values():
        for field in section['fields'].values():
            choices = field.get('choices')
            if choices and '' in choices:
                # Radix Select.Item reserves the empty value for clearing.
                # This legacy acknowledgement is optional, not an operator task.
                field.update(type='string', ui_type='text', choices=None,
                             hidden=True, disabled=True)
            elif choices == [False]:
                # Select serializes its value to a string, which is not False.
                field.update(type='boolean', ui_type='switch', choices=None, disabled=True)
            if field['name'] == 'config_version':
                field['disabled'] = True
            if field['type'] == 'object':
                # Official plugin-config has no JSON editor (falls back to Input).
                # Preserve advanced legacy tables; edit them in raw TOML mode.
                field.update(hidden=True, disabled=True)
            if field['type'] == 'object':
                # Official plugin-config has no JSON editor (falls back to Input).
                # Preserve advanced legacy tables; edit them in raw TOML mode.
                field.update(hidden=True, disabled=True)
    return schema
