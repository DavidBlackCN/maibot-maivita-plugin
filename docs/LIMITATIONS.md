# 已知限制

- H01：没有可信的静默回执例外，睡眠期间命令回复也可能被阻止。
- H02：缺少按插件归属撤销的频率接口，frequency_enabled 只允许 false，不改写 Host 全局频率。
- H03：不开放通用跨插件特权委托；公开 API 和模型 Tool 不能管理写入。官方 Host 有限命令仍可依据 MaiIdentity 本地名单执行。
- 只运行 life 引擎；旧 update_schedule_v4 Tool 不注册。未知 Planner schema 跳过注入。
- 模型超时或结果未知不自动重试，可能已产生供应商费用；预算限制调用尝试次数，不是费用保证。
- 不自动导入旧库或降级数据库。歧义迁移、未知 schema、损坏库和不匹配的引擎权威记录拒绝启动，不删库重建。
- 睡眠与 MaiHush 硬静音独立；正常发送守卫不能保证拦截绕过 Host 发送链的直接平台发送。

使用方法见 [README](../README.md)，接口边界见 [CONTRACTS](CONTRACTS.md)，恢复操作见 [ROLLBACK](ROLLBACK.md)。
