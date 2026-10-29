"""Read-only availability inference. Slow is display-only in scheme B."""
from dataclasses import dataclass

from ..domain.models import Category, require


@dataclass(frozen=True)
class AvailabilityConfig:
    enabled: bool = False
    slow_categories: tuple[str, ...] = ('study', 'work', 'travel', 'creative')
    busy_when_sleeping: bool = True
    busy_fatigue_threshold: int = 90
    busy_energy_threshold: int = 10
    followup_after_busy: bool = False

    def __post_init__(self):
        for key in ('enabled', 'busy_when_sleeping', 'followup_after_busy'):
            require(type(getattr(self, key)) is bool, 'invalid availability switch: '+key)
        require(self.followup_after_busy is False, 'busy followup is not supported in scheme B')
        require(isinstance(self.slow_categories, (tuple, list)) and
                all(c in tuple(Category) for c in self.slow_categories), 'invalid slow categories')
        object.__setattr__(self, 'slow_categories', tuple(self.slow_categories))
        for key in ('busy_fatigue_threshold', 'busy_energy_threshold'):
            value = getattr(self, key)
            require(type(value) is int and 0 <= value <= 100, 'invalid '+key)


@dataclass(frozen=True)
class Availability:
    state: str
    reason: str


CATEGORY_REASONS = {
    'rest': '正在休息', 'daily_life': '日常事务', 'entertainment': '正在娱乐',
    'study': '正在学习', 'work': '正在工作', 'social': '正在社交',
    'travel': '正在路上', 'creative': '正在创作', 'other': '当前活动',
}
LABELS = {'free': '空闲', 'slow': '半忙', 'busy': '忙碌'}


def derive_availability(state, activity, config, *, activity_visible=True):
    # Inference remains available when enforcement is off. Unknown body values
    # never imply exhaustion. A temporary wake still belongs to its sleep session.
    if config.busy_when_sleeping and state.active_sleep_session_id is not None:
        return Availability('busy', '睡眠中')
    if state.fatigue is not None and state.fatigue >= config.busy_fatigue_threshold:
        return Availability('busy', '疲劳过高')
    if state.energy is not None and state.energy <= config.busy_energy_threshold:
        return Availability('busy', '精力不足')
    if activity is None or activity.status != 'active':
        return Availability('free', '暂无当前活动')
    # Rolling segments currently use rest/other. Reuse the existing demanding
    # activity boundary so their energy_rate can also express a slow state.
    if activity.category in config.slow_categories:
        return Availability('slow', CATEGORY_REASONS.get(activity.category, '当前活动')
                            if activity_visible else '当前活动需要专注')
    if activity.energy_rate <= -0.05:
        return Availability('slow', '当前活动较耗能' if activity_visible else '当前活动需要专注')
    return Availability('free', CATEGORY_REASONS.get(activity.category, '当前活动')
                        if activity_visible else '当前可聊天')
