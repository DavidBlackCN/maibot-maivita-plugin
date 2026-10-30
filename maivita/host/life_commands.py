"""Finite user-facing life operations. No model output can select executable code."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import re
import shlex
import uuid

HELP='''MaiVita 生活管理（新增变更先预览，再确认）
/maivita replan（重新决定当前活动）
/maivita plan regenerate（重新生成今日参考计划）
/maivita sleep 30 或 麦麦管理 请休息30分钟
/maivita activity start study 阅读 / activity end
/maivita plan add 明天 09:00 10:00 阅读
/maivita plan update 条目ID 明天 10:00 11:00 阅读
/maivita plan remove 条目ID
/maivita goal add 学习Python / goal update 目标ID 新标题
/maivita goal pause|resume|cancel 目标ID
/maivita goal complete 目标ID 已结束活动ID
/maivita confirm|cancel|result 请求ID
中文示例：新增目标 学习Python；开始活动 阅读；结束当前活动；取消计划 条目ID。
使用 status、plan、goals、activity 查询公开状态。确认不会绕过睡眠、静音、预算或权限。'''

KINDS={'start_sleep','start_activity','end_activity','apply_plan_patch','manage_goal'}

def parse(text,engine,timezone='Asia/Shanghai'):
    if type(text) is not str or len(text)>256: raise ValueError('命令过长')
    match=re.fullmatch(r'(?:/maivita|麦麦管理)\s+(.+)',text.strip())
    if not match: raise ValueError('请使用 /maivita 或 麦麦管理 作为管理入口')
    body=match[1].strip()
    body=re.sub(r'^(?:请帮我|帮我|请)\s*','',body).rstrip('。！!')
    aliases={'结束当前活动':'activity end','重新规划':'replan','醒来':'wake','起床':'wake'}
    body=aliases.get(body,body)
    for a,b in [('新增目标 ','goal add '),('修改目标 ','goal update '),('暂停目标 ','goal pause '),
                ('恢复目标 ','goal resume '),('取消目标 ','goal cancel '),('完成目标 ','goal complete '),
                ('新增计划 ','plan add '),('调整计划 ','plan update '),('取消计划 ','plan remove '),
                ('开始活动 ','activity start other '),('确认 ','confirm '),('取消请求 ','cancel '),('查看结果 ','result ')]:
        if body.startswith(a): body=b+body[len(a):];break
    sleep=re.fullmatch(r'(?:休息|睡|睡觉)\s*(\d+)\s*(分钟|小时)',body)
    if sleep: body='sleep '+str(int(sleep[1])*(60 if sleep[2]=='小时' else 1))
    words=shlex.split(body)
    if words==['plan','regenerate']:return {'kind':'enqueue_replan','payload':{'reason':'authorized_command','task_kind':'planning'}}
    if words in (['wake'],['replan']):return {'kind':'wake' if words[0]=='wake' else 'enqueue_replan','payload':{'reason':'authorized_command'}}
    if len(words)==2 and words[0] in ('confirm','cancel','result') and re.fullmatch('[a-f0-9]{12}',words[1]):
        return {'control':words[0],'token':words[1]}
    if len(words)==2 and words[0]=='sleep':
        minutes=int(words[1])
        if not 5<=minutes<=1080: raise ValueError('睡眠长度须为5至1080分钟')
        return {'kind':'start_sleep','payload':{'duration_minutes':minutes,'kind':'nap' if minutes<180 else 'night_sleep','reason':'operator_confirmed'}}
    if words==['activity','end']:return {'kind':'end_activity','payload':{'reason':'operator_confirmed','status':'completed'}}
    if len(words)>=4 and words[:2]==['activity','start']:
        if words[2] not in ('rest','study','work','entertainment','daily_life','social','travel','creative','other'):raise ValueError('活动分类无效')
        return {'kind':'start_activity','payload':{'name':' '.join(words[3:]),'category':words[2],'visibility':'public_summary','check_after_minutes':45}}
    if len(words)>=3 and words[0]=='goal':
        action=words[1]; payload={'action':action}
        if action=='add':payload.update(action='create',goal_id=uuid.uuid4().hex,title=' '.join(words[2:]),visibility='public_summary')
        elif action=='update' and len(words)>=4:payload.update(goal_id=words[2],title=' '.join(words[3:]))
        elif action in ('pause','resume','cancel') and len(words)==3:payload['goal_id']=words[2]
        elif action=='complete' and len(words)==4:payload.update(goal_id=words[2],progress_evidence=words[3].split(','))
        else:raise ValueError('目标完成须提供真实已结束活动ID；其他用法见 help')
        if action!='add':
            with engine.database.read_view() as u:old=u.goals.get(payload['goal_id'])
            if old is None or old.visibility!='public_summary':raise ValueError('该目标不能通过公开命令管理')
        return {'kind':'manage_goal','payload':payload}
    if len(words)>=3 and words[0]=='plan':
        now=engine.clock.now_utc().astimezone(ZoneInfo(timezone)); action=words[1]
        if action=='remove' and len(words)==3:
            with engine.database.read_view() as u:old=u.items.get(words[2])
            if old is None or old.visibility!='public_summary':raise ValueError('该条目不能通过公开命令管理')
            return {'kind':'apply_plan_patch','payload':{'remove_ids':[words[2]],'upserts':[],'reason':'operator_confirmed'}}
        offset=3 if action=='update' else 2
        if action not in ('add','update') or len(words)<offset+4:raise ValueError('计划格式：plan add 日期 开始时间 结束时间 标题')
        day=words[offset]
        if day in ('今天','明天'):day=(now+timedelta(days=int(day=='明天'))).date().isoformat()
        def at(t):
            local=datetime.fromisoformat(day+'T'+t).replace(tzinfo=ZoneInfo(timezone))
            # Reject ambiguous/nonexistent civil times instead of guessing DST.
            if local.replace(fold=1).utcoffset()!=local.utcoffset():raise ValueError('时间处于时区切换，请选择明确时间')
            if local.astimezone(ZoneInfo('UTC')).astimezone(ZoneInfo(timezone)).replace(tzinfo=None)!=local.replace(tzinfo=None):raise ValueError('本地时间不存在')
            return local
        start,end=at(words[offset+1]),at(words[offset+2])
        if not now<start<end:raise ValueError('计划必须是未来且结束晚于开始')
        item={'item_id':words[2] if action=='update' else uuid.uuid4().hex,'name':' '.join(words[offset+3:]),
            'time_mode':'fixed','start_at':start.isoformat(),'end_at':end.isoformat(),'visibility':'public_summary'}
        if action=='update':
            with engine.database.read_view() as u:
                old=u.items.get(words[2])
            if old is None:raise ValueError('计划条目不存在')
            if old.visibility!='public_summary':raise ValueError('该条目不能通过公开命令管理')
            item['visibility']=old.visibility.value
            item['category']=old.category.value
            item['priority']=old.priority
            item['kind']=old.kind
            item['commitment_id']=old.commitment_id
        return {'kind':'apply_plan_patch','payload':{'local_date':day,'upserts':[item],'remove_ids':[],'reason':'operator_confirmed'}}
    raise ValueError('未识别或参数不完整；不会猜测执行。使用 /maivita help 查看示例')

def describe(operation):
    p=operation['payload'];kind=operation['kind']
    if kind=='start_sleep':return f"睡眠 {p['duration_minutes']} 分钟"
    if kind=='start_activity':return '开始公开活动：'+p['name']
    if kind=='end_activity':return '结束当前活动段（不代表目标或任务完成）'
    if kind=='enqueue_replan':return '重新生成今日参考计划' if p.get('task_kind')=='planning' else '重新决定当前活动'
    if kind=='manage_goal':return '目标 '+p['action']+'：'+p.get('title',p['goal_id'])+('（新目标公开可见）' if p['action']=='create' else '')
    if kind=='apply_plan_patch':return ('移除计划 '+','.join(p['remove_ids'])) if p['remove_ids'] else '计划 '+p['upserts'][0]['name']+' '+p['upserts'][0]['start_at']+' 至 '+p['upserts'][0]['end_at']+'（'+p['upserts'][0]['visibility']+'）'
    return kind
