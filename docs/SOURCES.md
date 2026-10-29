# 来源与许可

MaiVita 是在自主规划插件基础上修改的作品，维护者为 DavidBlackCN，当前版本见 [README](../README.md)。完整许可证见 [LICENSE](../LICENSE)；继承源码中的作者署名、版权注释和许可继续保留。

| 来源 | 固定版本 / 提交 | 继承与用途 |
|---|---|---|
| [DavidBlackCN/maibot-autonomous-planning-plugin](https://github.com/DavidBlackCN/maibot-autonomous-planning-plugin) | 5.1.0，`6c936467cfde6339c9eea91bbf761e3cf0a21fdc` | MaiVita 的直接源码基线，继承日程、固定睡眠、配置、服务与图标；保留 AGPL-3.0 和原作者信息 |
| [xuqian13/autonomous_planning_plugin](https://github.com/xuqian13/autonomous_planning_plugin) | 上述基线继承的项目历史 | 原作者靓仔 / xuqian13，自主规划、日程注入、角色裁判与主动行为实现 |
| [cateyemizuki/autonomous_planning_plugin](https://github.com/cateyemizuki/autonomous_planning_plugin/tree/01ce890f9b577d48fd5e3eb6eca1572a4d27df68) | 4.9.1，`01ce890f9b577d48fd5e3eb6eca1572a4d27df68` | cateye / cateyemizuki；选择性吸收并改写 SDK、Hook、权限与数据目录修复；AGPL-3.0 |
| [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501) | 1.3.12，`5c7637e6913c84d1c3d5e742fa9e95a7e70d3501` | 宿主接口参考；Host 源码不随本仓库分发，其许可为 GPL-3.0 |
| [maibot-plugin-sdk](https://pypi.org/project/maibot-plugin-sdk/2.15.0/) | 2.15.0 | 宿主提供的插件 SDK，未复制第三方包源码 |

MaiVita 自身修改包括 Life Runtime、新插件标识、生命周期适配、显式持久目录、单写入者、安全快照与旧 API 路由投影。当前入口只运行 life 引擎。原固定睡眠入口字节存于 tests/fixtures/provenance/legacy_plugin.r0.py 并校验哈希，作为来源存档保留。版本变化见 [UPDATE_NOTES](../UPDATE_NOTES.md)。

可公开测试中的 tests/fixtures/host_contracts 来自上表固定 Host，保留其 GPL-3.0 LICENSE，作为接口回归材料，不由插件运行入口导入。tests/fixtures/i0-source-lock.json 锁定公开归档及原始文件，下载缓存仍排除 Git。合成配置不含真实用户、聊天或凭据。Owner Auth 只作只读审计，未复制其源码；版本化契约为 MaiVita 自身声明，不冒称对方已有 API。

没有把旧仓库的 Git 历史、运行数据、日志、私有配置或凭据复制进本仓库。基线历史可在[上游更新日志](https://github.com/DavidBlackCN/maibot-autonomous-planning-plugin/blob/6c936467cfde6339c9eea91bbf761e3cf0a21fdc/CHANGELOG.md)查阅。
