"""Write a validated Vita configuration or convert Coordinate configuration offline."""
from pathlib import Path
import argparse,json,sys,tomllib
import tomli_w

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'ecosystem/coordinator'))
sys.path.insert(0,str(ROOT/'sources/maibot-maivita-plugin'));sys.path.insert(0,str(ROOT/'sources/maibot-maicoordinate-plugin'))


def convert(kind,data,*,fresh=False):
    from copy import deepcopy
    data=deepcopy(data)
    if kind=='coordinate' and not fresh:data.setdefault('plugin',{}).setdefault('config_version','1')
    if kind=='vita':
        from maivita.host.integration_config import IntegrationConfig
        result=IntegrationConfig.model_validate(data).model_dump(mode='json')
        notes=['每日规划仅为参考；实际活动由滚动决策推进。','从当前配置示例开始配置。',
            '共享生活预算：'+str(result['model']['daily_budget'])+' 次/角色时区自然日。',
            '使用当前版本新建测试实例。']
    else:
        from maicoordinate.config import Settings
        result=Settings.model_validate(data).model_dump(mode='json')
        notes=['保留群模式、路由和回执。','空间模式：'+result['space']['mode'],
            '互动白名单为空时不自动互动。Coordinate 名单须是 MaiTrace monitor.read_list 的子集。',
            '发布与浏览额度独立；未知结果不重试。']
    return result,notes


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('kind',choices=['vita','coordinate'])
    p.add_argument('--input',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.output.with_suffix('.changes.json').exists():raise ValueError('Output already exists')
    result,notes=convert(a.kind,tomllib.loads(a.input.read_text('utf-8')) if a.input else {},fresh=a.input is None)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:f.write(tomli_w.dumps(result))
    a.output.with_suffix('.changes.json').write_text(json.dumps({'changes':notes},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(a.output))


if __name__=='__main__':main()
