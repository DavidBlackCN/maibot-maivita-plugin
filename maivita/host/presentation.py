"""Public command text; APIs retain their structured contract."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

PHASES={'awake':'清醒','winding_down':'准备休息','sleeping':'睡眠中','temporarily_awake':'短暂醒来','waking':'正在醒来'}
def clean(value): return ' '.join(str(value).split())[:160]
def public_text(view,result,timezone):
    if not result.get('ok'): return 'MaiVita：当前无法完成操作（'+clean((result.get('error') or {}).get('code','NOT_READY'))+'）。'
    data=result['data']
    if view in ('wake','醒来','replan','重新规划'):
        return 'MaiVita：'+('已唤醒；仍遵守群静音。' if view in ('wake','醒来') else '已提交重新规划；睡眠、预算和冷却约束仍生效。')
    if view=='status':
        lines=['MaiVita：'+PHASES.get(data.get('sleep_phase'),'状态待确认')]
        activity=data.get('activity')
        lines.append('当前活动：'+(clean(activity['name']) if activity else '暂无'))
        lines.append('计划：'+('尚未生成' if data.get('plan_version',0)==0 else '已生成，可用 /maivita plan 查看'))
        if data.get('next_deadline_at'):
            deadline=datetime.fromisoformat(data['next_deadline_at']).astimezone(ZoneInfo(timezone))
            lines.append('下次状态检查：'+deadline.strftime('%m-%d %H:%M')+'（'+timezone+'）')
        if data.get('degraded_reason')=='history_incomplete': lines.append('生活历史尚少，精力等数值暂不估算。')
        return '\n'.join(lines)
    if view=='plan':
        lines=['MaiVita 当前计划']
        lines.extend('• '+clean(item.get('name','未命名活动')) for item in data.get('items',[])[:20])
        if len(lines)==1: lines.append('暂无计划。可在插件设置中开启每日生活规划。')
        return '\n'.join(lines)
    if view=='availability':
        return 'MaiVita：'+PHASES.get(data.get('sleep_phase'),'状态待确认')+'；'+('可以参与聊天，仍遵守静音约束。' if data.get('can_reply_recommendation') is True else '当前不宜打扰。')
    return json.dumps(result,ensure_ascii=False)
