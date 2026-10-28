# MaiVita 0.4.1：官方 Host 命令，无需补丁

支持精确组合 Host 1.3.12 / SDK 2.15.0、Host 1.3.15 / SDK 2.16.0。默认安装启用生活引擎，沿用已有数据库及配置。无需填写实例确认串、锁路径或测试群 ID。

- `/maivita status`、`/maivita plan`、`/maivita availability`：公开查询；睡眠或硬静音可能阻止回复。
- `model.daily_planning_enabled = true`：每天一次持久化规划触发，遵守睡眠、预算与冷却。默认关闭，开启将使用配置的模型。
- `/maivita wake` / `/maivita 醒来`、`/maivita replan` / `/maivita 重新规划`：直接使用官方 Command 入口，无需 Host 补丁；在 MaiIdentity 3.1.1 的 `[management] vita_admin_accounts` 填入允许管理的 QQ 号并重载 Identity 即可。
- 现有 MaiCoordinate 0.2.1 聚合 Vita 0.4.1、Identity 3.1.1 和 Hush 0.2.1。默认影子模式，`mode = "controlled"` 才允许受控醒来表达。

管理发言者从本插件收到的实际 Host 命令消息中取得。文本、账号、Bot、群号、平台与会话必须一致，API 参数、模型生成的身份、自称 owner、旧补丁引用均不能代替这一入口。Identity 只回答权限规则是否允许，Vita 本地执行有限动作；缺失 Identity、查询超时、权限拒绝、配置/生命周期改变或状态版本冲突都拒绝执行。请求键在 Vita 本地根据原始消息生成，重载后仍防重放。

唤醒不解除 Hush 硬静音；重新规划仅入队，仍受睡眠、预算、冷却约束。确认文本走普通 SendService，没有静音豁免。只读策略查询不是原子跨插件授权事务，不提供通用特权委托。公开写 API 和自然语言管理 Tool 继续关闭。

H01/H02、通用 H03、当前源码的付费模型完整 I1、真实 QQ 和生产验收未完成；本版隔离测试不代表这些通过。安装不修改 Host，已安装的旧实验补丁不再需要；恢复官方核心请使用原版对应提交并保留自身合法改动，勿直接清空服务器工作区。
