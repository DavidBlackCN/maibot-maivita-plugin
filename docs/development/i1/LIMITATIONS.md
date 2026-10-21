# I1 限制

- H01：Host 没有可信管理回执端到端契约。睡眠时不设置任何发送例外，包括本插件命令；普通发送仍经 SDK/Host 权限和 Hook，失败不会报告已发送。发送静默 Hook 出错时采用 abort。无绕过标志或共享流许可。
- H02：归属频率撤销不可用；本包不声明/调用 frequency 能力，配置只能 false，不复原全局频率。
- H03：Owner Auth 无身份声明 API；本包不宣称已集成，Owner/QQ/提示词不能授权。request_replan/request_wakeup 及旧写入 Tool 明确拒绝；不提供公开管理写入绕过。可由测试配置 generate_on_start 触发模型决策，不能借此充当用户授权。
- H04：加载失败插件自行释放 Runtime、worker、coordinator 和两层写入锁。worker 运行失败显示降级，不降级到旧调度器；需要卸载后重载。
- 只有一个角色/default；配置与模型仅在重载时启用，禁止同时运行旧、新调度器。无生产激活或迁移路径。
- 模型输出必须是严格 JSON 候选；不把 reasoning、计划、过期结果当成实际事实。不重试不确定结果；既有 deadline 恢复无需模型。
- 只读公开 API 与注入来自同一新库，不接受参数扩大隐私。无跨插件身份、策略、社交或记忆生态验收。
- Planner 仅在实际提供 extra_prompt 形状时注入；typed items/messages 暂不重写，未知形状安全跳过。Replyer 保留其他插件字段并替换自身标记块。
- 全平台、多 Hook、真实模型质量与 24 小时/7 天门禁需分别验收。开发版通过首次安装测试不等于 R0–R4 正式通过，更不等于生产许可。
