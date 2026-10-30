"""WebUI presentation only; the runtime configuration stays authoritative."""
from maibot_sdk.config import generate_plugin_config_schema
from .integration_config import IntegrationConfig


HIDDEN = {
    'plugin': {'config_version'},
    'instance': {'acknowledgement', 'writer_lock', 'frequency_enabled'},
    'model': {'mode', 'task', 'model', 'timeout_seconds', 'max_tokens', 'generate_on_start'},
    'routine': {'initialize_body_state', 'weekly_pattern'},
    'scheduler': {'enabled', 'daily_decision_budget', 'change_chance_percent'},
    'availability': {'followup_after_busy', 'busy_when_sleeping'},
}
SECTIONS = {'plugin': '插件', 'instance': '会话与时区', 'model': '生活背景与调用额度',
            'routine': '作息偏好', 'scheduler': '高级：活动检查', 'availability': '高级：专注与休息'}
LABELS = {
    'enabled': '启用', 'allowed_streams': '作用会话（留空沿用宿主范围）', 'timezone': '角色时区',
    'daily_budget': '每日生活模型调用上限', 'life_context': '补充生活背景（可留空）',
    'daily_planning_enabled': '生成每日参考计划', 'daily_planning_time': '每日参考计划时间',
    'preferred_sleep_time': '偏好入睡时间', 'preferred_wake_time': '偏好起床时间',
    'decision_interval_minutes': '活动检查间隔（分钟，不等于模型调用间隔）',
    'segment_min_minutes': '最短活动时长（分钟）', 'segment_max_minutes': '最长清醒活动时长（分钟）',
    'segment_sleep_max_minutes': '睡眠会话最长时长（分钟）', 'slow_categories': '需要专注的活动类别',
    'busy_fatigue_threshold': '休息拦截的疲劳阈值', 'busy_energy_threshold': '休息拦截的精力阈值',
}

def build_schema(**metadata):
    schema = generate_plugin_config_schema(IntegrationConfig, **metadata)
    for section_name,section in schema['sections'].items():
        section['title'] = SECTIONS[section_name]
        section['collapsed'] = section_name in ('scheduler', 'availability')
        section['description'] = ('默认值即可使用；隐藏的路由与兼容参数可在原始 TOML 中维护，详见 README。'
                                  if section_name == 'model' else section.get('description', ''))
        for field in section['fields'].values():
            field['label'] = LABELS.get(field['name'], field.get('label', field['name']))
            if section_name == 'availability' and field['name'] == 'enabled':
                field['label'] = '身体极度疲劳时暂停回复（默认关闭，睡眠静默独立生效）'
            if field['name'] in HIDDEN.get(section_name, set()):
                field['hidden'] = True
            choices = field.get('choices')
            if choices and '' in choices:
                # Radix Select.Item reserves the empty value for clearing.
                # This historical acknowledgement is optional, not an operator task.
                field.update(type='string', ui_type='text', choices=None,
                             hidden=True, disabled=True)
            elif choices == [False]:
                # Select serializes its value to a string, which is not False.
                field.update(type='boolean', ui_type='switch', choices=None, disabled=True)
            if field['name'] in ('weekly_pattern','daily_decision_budget','change_chance_percent'):
                field.update(hidden=True,disabled=True)
            if section_name=='scheduler' and field['name']=='enabled':
                field.update(hidden=True,disabled=True)
            if field['name'] == 'config_version':
                field['disabled'] = True
            if field['type'] == 'object':
                # Official plugin-config has no JSON editor (falls back to Input).
                # Preserve nested tables; edit them in raw TOML mode.
                field.update(hidden=True, disabled=True)
    return schema
