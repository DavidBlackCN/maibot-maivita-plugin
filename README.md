# MaiVita 0.5.2

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


### 2026-10-28 WebUI 配置兼容修复

修复空字符串枚举导致的 Select.Item 崩溃，以及必填账户条目无法生成编辑 Schema。历史确认字段不再显示为空选项；固定关闭的特权字段只读显示。运行时模型和权限校验不变。高级嵌套角色/旧引擎表请使用原始 TOML 编辑，避免 Host 表单把对象转成字符串。

若 Identity 页面提示缺少 plugin.config_version，在现有 [plugin] 内补入 config_version = "3" 后保存并重载，保留原有其他配置。Vita 使用 "3"，Coordinator 使用 "1"。这些是配置格式版本，不是插件发布版本；不能填写 3.2.2 或 0.5.2。不要删除数据或只保留 enabled。当前源码真实模型 I1 和 QQ 验收仍待进行，不沿用旧版本通过记录。
