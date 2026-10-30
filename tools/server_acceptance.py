"""Run from the EXISTING Host admin Python context; never start another Host.

Default: await run(supervisor). Explicit publication/interaction requires execute=True
and the exact own account / interaction target. Results may include public text.
"""
import asyncio

VITA='davidblackcn.maibot-maivita-plugin'
COORD='davidblackcn.maibot-maicoordinate-plugin'
TRACE='Rabbit-Jia-Er.MaiTrace'


async def run(supervisor,*,execute=False,publish_account=None,interaction_target=None):
    async def call(plugin,name,args=None,timeout=10):
        response=await supervisor.invoke_api(plugin,name,args=args or {},timeout_ms=int(timeout*1000))
        if response.error or not response.payload.get('success'):raise RuntimeError(str(response.error or response.payload))
        return response.payload['result']
    results={'life':await call(VITA,'get_public_life_context'),'coordinate':await call(COORD,'get_status'),
        'provider':await call(TRACE,'get_space_contract'),'writes_requested':False}
    if not execute:
        if publish_account or interaction_target:raise ValueError('Target supplied without execute=True')
        return results
    if not publish_account and not interaction_target:raise ValueError('Explicit test target required')
    for target in (publish_account,interaction_target):
        if target is not None and (not isinstance(target,str) or not target.isascii() or not target.isdigit()):
            raise ValueError('Exact QQ account required')
    results['writes_requested']=True
    contract=results['provider']
    if not contract.get('ready'):raise ValueError('Provider configuration is not ready')
    if publish_account:
        data=results['life']['data'];activity=data.get('activity')
        if not activity:raise ValueError('No public current activity; wait for a real observation')
        results['publication']=await call(TRACE,'publish_topic_api',
            {'life_basis':{'activity':activity,'current':True},'expected_account':publish_account},contract['budgets']['post']+20)
    if interaction_target:
        if interaction_target not in contract['interaction_whitelist']:raise ValueError('Target not explicitly whitelisted')
        results['interaction']=await call(TRACE,'browse_once_api',
            {'max_feeds':5,'interaction_whitelist':[interaction_target]},contract['budgets']['browse']+20)
    return results


if __name__=='__main__':
    import argparse,json,shutil,uuid
    from pathlib import Path
    import tomli_w
    p=argparse.ArgumentParser(description='Create a one-shot acceptance plugin for the existing Host. Default is read-only.')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true')
    p.add_argument('--publish-account');p.add_argument('--interaction-target');a=p.parse_args()
    if a.execute and not (a.publish_account or a.interaction_target):p.error('Explicit test target required')
    if not a.execute and (a.publish_account or a.interaction_target):p.error('Targets require --execute')
    a.output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__).with_name('server_probe_plugin.py'),a.output/'plugin.py')
    shutil.copyfile(__file__,a.output/'server_acceptance.py')
    (a.output/'config.toml').write_text(tomli_w.dumps({'plugin':{'config_version':'1','enabled':True},
        'execute':a.execute,'operation_id':uuid.uuid4().hex,'publish_account':a.publish_account or '',
        'interaction_target':a.interaction_target or ''}),encoding='utf-8')
    (a.output/'_manifest.json').write_text(json.dumps({'manifest_version':2,'id':'davidblackcn.phase1-server-probe',
        'name':'Phase 1 server acceptance','version':'1.0.0','description':'Optional one-shot status / explicitly targeted space acceptance.',
        'author':{'name':'DavidBlackCN','url':'https://github.com/DavidBlackCN'},
        'urls':{'repository':'https://github.com/DavidBlackCN/maibot-maivita-plugin','homepage':'https://github.com/DavidBlackCN'},
        'i18n':{'default_locale':'zh-CN','supported_locales':['zh-CN'],'locales_path':'_locales'},
        'license':'AGPL-3.0','host_application':{'min_version':'1.3.12','max_version':'1.3.18'},
        'sdk':{'min_version':'2.15.0','max_version':'2.19.0'},'capabilities':['api.call']}),encoding='utf-8')
    (a.output/'_locales').mkdir();(a.output/'_locales/zh-CN.json').write_text('{}',encoding='utf-8')
    print('Prepared '+str(a.output)+'. Load it after the core plugins in the existing Host; read its own data/plugins/davidblackcn.phase1-server-probe/*.json, then unload it.')
