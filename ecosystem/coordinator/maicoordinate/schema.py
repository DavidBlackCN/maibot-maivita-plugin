"""SDK 2.15 WebUI drafts; runtime configuration always uses strict Settings.

The SDK requires default-constructible list items to render an editor. Empty
drafts are UI placeholders only: they never supply a valid runtime route.
"""
from copy import deepcopy
from maibot_sdk import PluginConfigBase
from maibot_sdk.config import generate_plugin_config_schema
from pydantic import Field, create_model
from .config import Route, Settings


def build_schema(**metadata):
    fields={}
    for name,info in Route.model_fields.items():
        draft=deepcopy(info)
        if draft.is_required(): draft.default=''
        fields[name]=(info.annotation,draft)
    draft_route=create_model('RouteDraft',__base__=PluginConfigBase,**fields)
    draft_settings=create_model('SettingsDraft',__base__=Settings,
        routes=(list[draft_route],Field(default_factory=list,max_length=4)))
    return generate_plugin_config_schema(draft_settings,**metadata)
