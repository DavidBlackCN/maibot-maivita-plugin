# MaiVita 0.4.0 联动使用说明

支持精确组合 Host 1.3.12 / SDK 2.15.0、Host 1.3.15 / SDK 2.16.0。默认安装启用生活引擎，状态命令改为可读文本，API 仍返回结构化数据。无需填写实例确认串、锁路径或测试群 ID；适配器允许进入的会话受 Host 正常权限控制。

- `/maivita status`、`/maivita plan`、`/maivita availability`：公开查询，睡眠或硬静音可能阻止回复。
- 设置 `model.daily_planning_enabled = true`：每个本地日期最多新增一次持久化规划触发；睡眠、模型预算及冷却继续生效。重启不会重复同一天的模型尝试。此开关会使用配置的真实模型，默认关闭。
- 现有 MaiCoordinate 0.2.0 可只读聚合 Vita、Identity 和 Hush。默认影子模式，无主动生成和发送；选择 `mode = "controlled"` 后才允许受控醒来表达。
- `/maivita wake`（或 `/maivita 醒来`）及 `/maivita replan`（或 `/maivita 重新规划`）：必须安装单独验证的 Host command-context v1 扩展，并由 MaiIdentity 本地配置明确授权。官方 Host 无此能力时拒绝执行。聊天中的 owner、自称身份、普通 Tool 或 API 参数不能授权。

唤醒不解除 Hush 的硬静音；重新规划仅入队，仍受睡眠、预算、冷却和状态版本约束。确认文本走普通发送，没有管理回执豁免。公开写 API 与通用自然语言工具管理保持关闭。

H01 可信回执、H02 所有权频率覆盖和通用 H03 原子远端消费仍未完成。可选 Host 扩展只实现有限命令链，不代表官方 Host 或生产平台已验收。新源码的合成测试、原生 Host 生命周期和真实模型 I1 必须分别记录；历史付费模型通过记录不可复用为本版完整 I1。
