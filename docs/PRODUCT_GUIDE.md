# 超级麦麦插件成品候选使用说明

本轮组合：MaiVita 0.5.2、MaiIdentity 3.2.2、MaiHush 0.2.2、现有 MaiCoordinate 0.3.1。使用官方 Host，不需要私有补丁，也没有新增插件。支持的精确配对为 Host 1.3.12 / SDK 2.15.0 和 Host 1.3.15 / SDK 2.16.0；中间版本、混搭和未来版本不自动放行。

## 安装与最少设置

停止 Bot，保留各插件的 `config.toml` 和 `data/plugins`，分别替换代码目录；每个插件 ID 只能安装一份。使用 Bot 自己的 `.venv/bin/python`。首次安装让 Host 生成默认配置，不要填写测试确认串、锁路径、实例 ID 或内部会话 ID。Vita 与 Identity 使用实际接入的消息；Hush 保留你已有的受管理群 routes 配置，Coordinator 只会发现并核对这些 Hush 已管理群，不会自动接管其他群。

普通状态、身份登记、关系确认和静音管理可直接使用。涉及管理员操作，只需在 Identity 本地配置允许管理生活的真实 QQ 号：

```toml
[management]
vita_admin_accounts = ["你的QQ号"]
```

修改后重载 Identity。默认空名单不给任何人管理权；群主称号、关系、昵称、模型生成的内容都不替代此配置。Hush 继续使用自身 routes 的 owner_accounts/admin_accounts，不从 Identity 推导静音管理员。

生活自动规划需要把 Vita 的 `[model] daily_planning_enabled = true`。主动表达需要把 Coordinator 的 `mode = "controlled"`，其 `model_task` 指向 Host 已配置的任务（默认 utils）。两项默认不开启模型费用；所有密钥只放在 Host 模型配置中。先确认已有任务可用，再重载相应插件。

## 生活管理

`/maivita help` 显示完整帮助，`status`、`plan`、`goals`、`activity` 查看公开事实与条目 ID。

| 操作 | 示例 |
| --- | --- |
| 睡眠 | `/maivita sleep 5` 或 `麦麦管理 请休息5分钟` |
| 开始活动 | `/maivita activity start study 阅读` 或 `麦麦管理 开始活动 阅读` |
| 结束活动 | `麦麦管理 结束当前活动` |
| 新增计划 | `/maivita plan add 明天 09:00 10:00 阅读` |
| 调整计划 | `/maivita plan update 条目ID 明天 10:00 11:00 阅读` |
| 删除未开始计划 | `麦麦管理 取消计划 条目ID` |
| 目标 | `麦麦管理 新增目标 学习Python`；`/maivita goal update 目标ID 新标题` |
| 目标状态 | `/maivita goal pause 目标ID`，也支持 resume、cancel |
| 完成目标 | `/maivita goal complete 目标ID 已结束活动ID` |

以上新增管理操作先返回明确预览与请求 ID。由同一账号在同一会话两分钟内发送 `/maivita confirm 请求ID`；`cancel` 取消，`result` 查看已持久化结果。确认时重新检查权限与状态版本；状态改变则拒绝旧预览，需要重新提出操作。重复确认、重载或发送回执丢失不重复修改。记录状态不确定时不自动重试。

自然语言是明确命令前缀后的有限语法，不把任意闲聊或模型输出解释成管理指令。日期支持今天、明天或 YYYY-MM-DD；时间使用实例时区，拒绝模糊的夏令时。新目标和活动公开可见，预览会注明；公开命令不能编辑私密计划和目标。计划不等于已经发生，完成目标必须引用真实已结束活动，不补造历史。

原有 `/maivita wake`、`/maivita replan` 继续是直接的有限管理员命令。睡眠仍受时长、冷却和既有承诺约束；唤醒不解除硬静音；重新规划仍受模型预算与睡眠约束。睡眠和静音期间回复可能被拦截，可在恢复后用 result 查询；没有回执豁免。

## 身份与关系

`/maiidentity register` 或 `/maiidentity 登记我` 仅登记当前实际 QQ 账号。双方登记后，发送 `/maiidentity 建立关系 对方QQ 朋友`，对方使用 `/maiidentity 确认关系 关系ID`。也支持家人、伴侣（用户间公开声明，不是与 Bot 的关系）。确认有效期为 24 小时，确认后的关系持续到任一方撤销。`/maiidentity 查看关系` 查询，`/maiidentity 撤销关系 关系ID` 撤销。

不能代登记、代确认、自动合并账号或从自称恢复已撤销身份。公开关系进入当前群的只读风格摘要，私密关系、账号名单和主体 ID 不进入协调层。存在争议时使用中性表达。关系不给管理权限。

## 静音与主动表达

Hush 保持原有命令和权限。新增同入口中文写法：`/maihush 安静5分钟`、`/maihush 续期5分钟`、`/maihush 恢复聊天`。普通成员不能借中文写法获得硬静音或解禁权。硬静音和 Vita 睡眠独立生效。

Coordinator 默认 shadow，不调用模型或发送；controlled 模式可处理醒来、活动开始、活动结束、计划调整四种公开事件。`social_event_types` 可以缩小事件范围。一个群默认一小时冷却，多个积压事件合并为当前机会，过期事件不补发。生活事实、当前群公开关系、最近三次确认发出的事件类型共同参与表达，不能编造长期记忆或已经完成的计划。

Vita 规划与 Coordinator 表达共同读取 Host 的 nickname、personality、behavior_style、reply_style；Vita life_context 和 Coordinator character_context 仅作各自补充。Planner schema 1 使用追加公开上下文的方式，保留原消息、工具调用和其他插件内容；未知 schema 跳过，不重写历史。公开上下文不提供执行权限。

发送前重新检查生活、静音、路由、人设和关系摘要版本。模型失败或状态变化不会退款并立即重试；发送结果不确定保留 unknown，不自动重发。主记忆系统未提供的跨插件写接口没有被伪造；本版连续性来自自身公开生活事实与发送回执。

## 升级、备份和回滚

交付目录的 `product_bundle.py` 使用标准库：

```bash
.venv/bin/python /path/to/delivery/product_bundle.py check --bundle /path/to/delivery --host /path/to/MaiBot
# 完全停止 Bot 后备份，输出目录必须是新的、且在 Bot 目录外：
.venv/bin/python /path/to/delivery/product_bundle.py backup --host /path/to/MaiBot --output /path/to/new-backup --stopped
# 回滚先生成新的恢复目录，不覆盖运行实例：
.venv/bin/python /path/to/delivery/product_bundle.py restore --backup /path/to/new-backup --output /path/to/new-restore
```

备份获取插件写锁并通过 SQLite backup 保存一致快照，包含插件代码、配置和各自数据，不备份 Host 聊天库。恢复后，停机把相应插件代码、配置、数据成套替换；当前数据先另存，不能把旧数据库叠在新 WAL 上。不要删除去重账本来“修复” unknown。

本版不改变已有生活、身份、静音、协调数据库 schema。Vita 增加独立 `life/management.sqlite3` 确认账本。管理账本上限 10,000 条；协调账本默认 10,000 条，可提高至 100,000。达到上限停止接收新动作并报告，不静默遗忘去重。运维备份应含新增账本。

## 验收边界

本版交付目标是功能和代码完成、自动测试可追溯，交给操作者进行快速真实 Bot 验收。当前源码的真实模型 I1、真实 QQ、生产部署结论必须分别记录，不能沿用旧版本或把合成驱动当成 QQ。长时间观察可在后续日常使用进行，不是本轮人工验收需等待七天的前置条件。

H01 静音回执例外、H02 频率所有权覆盖、通用 H03 跨插件特权委托仍不实现且不开启，已从本版目标依赖中排除。官方命令入口的有限管理可以工作；它不是通用跨插件原子授权。官方 Host 更新后应先验证配对和契约，不建议删除版本检查强行启动。


### 2026-10-28 WebUI 配置兼容修复

修复空字符串枚举导致的 Select.Item 崩溃，以及必填账户条目无法生成编辑 Schema。历史确认字段不再显示为空选项；固定关闭的特权字段只读显示。运行时模型和权限校验不变。高级嵌套角色/旧引擎表请使用原始 TOML 编辑，避免 Host 表单把对象转成字符串。

若 Identity 页面提示缺少 plugin.config_version，在现有 [plugin] 内补入 config_version = "3" 后保存并重载，保留原有其他配置。Vita 使用 "3"，Coordinator 使用 "1"。这些是配置格式版本，不是插件发布版本；不能填写 3.2.2 或 0.5.2。不要删除数据或只保留 enabled。当前源码真实模型 I1 和 QQ 验收仍待进行，不沿用旧版本通过记录。
