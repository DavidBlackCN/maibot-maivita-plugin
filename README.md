# MaiVita 0.5.0

MaiBot 的持久生活引擎：活动、计划、目标、睡眠、历史与公开上下文。本版支持官方 Host 命令管理与有限中文管理，使用现有 MaiIdentity 权限规则；无需私有 Host 补丁。

- 默认启用 life，安装后 `/maivita status` 可用。无需手填会话 ID、确认串或锁路径。
- `/maivita help` 查看生活管理；新增变更先预览，再由同一账号确认。完成目标需要真实活动证据。
- 睡眠和 Hush 硬静音独立；公开 API/模型 Tool 不提供管理写权限。
- 规划与主动表达读取同一组官方 Host 人设配置；Planner 和 Replyer 读取公开生活事实。
- 配置版本 3；生活 schema 4；新增独立确认账本 schema 1。保留已有配置和数据升级。

支持精确组合 Host 1.3.12 / SDK 2.15.0、Host 1.3.15 / SDK 2.16.0。

完整安装、命令、权限、模型开关、备份及回滚见 [产品指南](docs/PRODUCT_GUIDE.md)。当前自动化证据见 [验收说明](docs/ACCEPTANCE.md)，源码导航见 [SOURCE_LAYOUT](docs/SOURCE_LAYOUT.md)。较早的 development 文档仅描述对应阶段，不覆盖本版使用方式。

自动规划与主动表达需要显式启用已有模型任务；默认不新增模型费用。H01/H02/通用 H03 保持关闭。当前源码真实模型、真实 QQ、生产部署由独立人工验收确认，旧版通过记录不代表本版通过。

现有协调层源码与联合 CI 收录在 [ecosystem](ecosystem/README.md)，没有新增插件。

保留原项目署名及许可证，来源见 [SOURCES](docs/SOURCES.md) 和 [LICENSE](LICENSE)。
