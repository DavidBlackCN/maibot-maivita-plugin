# MaiVita

为 MaiBot 提供持久的生活状态、活动、计划、目标与睡眠，让日常回复感知角色正在做什么。

- 当前版本：`0.8.2`
- MaiBot 版本范围：`1.3.12–1.3.15`
- Plugin SDK 版本范围：`2.15.0–2.16.0`
- 项目地址：[MaiVita](https://github.com/DavidBlackCN/maibot-maivita-plugin)

仅支持精确配对 **Host 1.3.12 / SDK 2.15.0**、**Host 1.3.15 / SDK 2.16.0**，不支持中间版本或交叉配对。无需私有 Host 补丁。

## 功能

### 日程规划与生活状态

根据 Host 的昵称、人设、行为和回复风格生成每日计划，向 Planner 和 Replyer 提供公开生活上下文。计划代表打算做的事，活动历史代表实际发生的事；完成目标需要引用已结束活动，不能把计划直接当成经历。

每日规划默认开启，使用 Host 的 `planner` 模型任务，**会产生模型调用费用**。按角色时区每天 08:00 触发，晚启动会补触发当天规划；重启不会重复已调用的规划。每次模型调用尝试均计入滚动 24 小时预算，包括失败重试。

### 可确认的生活管理

管理员可以新增或调整计划、管理目标、开始和结束活动、请求睡眠。新增管理操作先预览，再由同一账号在同一会话两分钟内确认；状态或权限发生变化时，旧预览会被拒绝。确认结果持久保存，重复确认不会重复修改。

### 睡眠与静音

睡眠时普通出站消息会被守卫拦截，包括命令回执。MaiHush 的硬静音独立生效：唤醒不会解除硬静音，解除硬静音也不会唤醒。作息配置是模型偏好，不是强制睡眠时刻。

忙闲三态功能默认关闭，可通过 `[availability] enabled = true` 开启。`/maivita status` 显示三态、原因及开关状态：学习、工作、出行、创作为 slow；其他活动能耗率 `energy_rate <= -0.05` 也为 slow；其余为 free。slow 与 free 均正常回复。活跃睡眠会话或身体达到极值（默认疲劳 ≥ 90、精力 ≤ 10）优先推导为 busy，启用后出站守卫返回 abort。类别与身体阈值可配置。

本阶段采用方案 B：不延迟、不缓存、不补发消息，`followup_after_busy` 仅支持 `false`。关闭忙闲守卫或 `busy_when_sleeping` 不会解除原有睡眠静默，MaiHush 仍独立决定是否拒绝发送。两个 EARLY 守卫不依赖先后顺序；任一个 abort 都终止发送，continue 也不会绕过另一个守卫。busy 时 status 命令回执同样可能被拦截；公开 availability API 可只读查看三态，未知身体值不触发极值规则。

### 可选主动表达

**MaiCoordinate 是可选扩展**：不安装也能使用 MaiVita、MaiIdentity、MaiHush 三核心功能。需要根据公开生活事件主动发言时，可另行安装 MaiCoordinate；其默认 shadow 模式不调用模型、不发送消息，主动表达须显式选择 controlled。

## 安装

### 1. 放置插件

停止 MaiBot，将插件放入 `plugins`，同一插件 ID 只保留一份。超级麦麦三核心组合安装 MaiVita、MaiIdentity、MaiHush；MaiCoordinate 按需安装。

```text
MaiBot/
└── plugins/
    └── maibot-maivita-plugin/
        ├── _manifest.json
        ├── plugin.py
        └── maivita/
```

使用 MaiBot 自己的 Python 环境，确认已安装 Manifest 声明的 `pillow>=9.0.0` 与 `tzdata>=2024.1`。停止旧的完整自主规划插件，避免同时维护生活状态。

### 2. 检查模型与管理员

确认 Host 的 `model_config.toml` 已配置 `planner` 任务。若暂不需要自动规划，在首次加载前按示例准备配置，将 `daily_planning_enabled` 设为 `false`，并保持 `generate_on_start = false`。

生活管理权限唯一来自 **MaiIdentity 的 config.toml**：

```toml
[management]
# 替换为真实 QQ 账号；空数组表示无人拥有 Vita 管理权
vita_admin_accounts = ["你的真实QQ号"]
```

保存后重载 MaiIdentity。群主称号、昵称、自称和关系都不授予管理权。MaiHush 使用自己路由里的 `owner_accounts` / `admin_accounts`，两套名单独立。

### 3. 启动与查看

首次加载由 Host 生成配置；无需填写确认串、写锁路径或会话 ID。发送 `/maivita help` 或 `/maivita status` 查看状态。状态包含今日计划、模型调用和不回复原因；睡眠或硬静音期间可能无法收到回执。

### 从旧版本更新

停机备份代码、`config.toml` 和完整插件数据目录，再替换代码。保留现有数据与写锁设置；配置版本为 `"3"`，使用 `plugin`、`instance`、`model`、`routine`、`scheduler`、`availability` 六段。旧 `owner_auth_enabled` / `privileged_writes_enabled` 无论真假均被忽略，不产生权限；其他未知字段仍会报错。

不自动导入旧规划库或降级数据库。数据中的非 life 权威记录、未知 schema 和损坏库会阻止启动。备份应包含 `life/maivita.db`、`life/management.sqlite3` 与 `engine-authority.json`；恢复须使用匹配的代码、配置和完整数据，不能覆盖正在运行的数据库。详见[回滚指南](docs/ROLLBACK.md)。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/maivita help` | 查看帮助 |
| `/maivita status` | 查看生活状态、规划与模型调用情况 |
| `/maivita plan`、`/maivita goals`、`/maivita activity` | 查看公开计划、目标、当前活动及 ID |
| `/maivita sleep 5` | 预览休息 5 分钟的请求 |
| `/maivita activity start study 阅读` | 预览开始活动 |
| `麦麦管理 结束当前活动` | 预览结束活动 |
| `/maivita plan add 明天 09:00 10:00 阅读` | 预览新增计划 |
| `/maivita plan update 条目ID 明天 10:00 11:00 阅读` | 预览调整计划 |
| `麦麦管理 取消计划 条目ID` | 预览取消未开始计划，保留历史 |
| `麦麦管理 新增目标 学习Python` | 预览新增目标 |
| `/maivita goal update 目标ID 新标题` | 预览修改目标 |
| `/maivita goal pause 目标ID` | 暂停目标；也支持 resume、cancel |
| `/maivita goal complete 目标ID 已结束活动ID` | 引用活动证据完成目标 |
| `/maivita confirm 请求ID` | 确认预览，重新检查权限与状态 |
| `/maivita cancel 请求ID`、`/maivita result 请求ID` | 取消请求、查看持久化结果 |
| `/maivita wake`、`/maivita replan` | 管理员直接唤醒、请求重新规划 |

日期支持今天、明天或 `YYYY-MM-DD`，时间按角色时区。中文管理仅识别 `麦麦管理` 前缀后的有限语法，不执行普通闲聊里的指令。新目标和活动公开可见；公开命令不能编辑私密计划与目标。睡眠有时长、承诺冲突及冷却约束，唤醒后五分钟内不能开启新的睡眠会话。回执被静音拦截时，恢复后用 `result` 查询，不要重复提交。

## 配置

完整字段、默认值和范围见 [config.example.toml](config.example.toml)。配置格式版本与插件发布版本不同。

### 会话与时区

```toml
[instance]
# 空数组跟随 Host/适配器允许的全部会话；非空为精确 stream_id 白名单
allowed_streams = []
# 日程时间和每日规划使用的 IANA 时区
timezone = "Asia/Shanghai"
```

`writer_lock` 留空使用授权数据目录或原权威记录的锁路径，正常安装无需填写；`acknowledgement` 是历史兼容项，留空即可。`frequency_enabled` 只能为 `false`，不修改 Host 全局频率。

### 模型与每日规划

```toml
[model]
# task 跟随 Host 任务路由；model 指定 Host 中已有的模型
mode = "task"
task = "planner"
model = ""
# 滚动 24 小时最多 16 次调用尝试；失败也计入
daily_budget = 16
# 默认每日规划开启，会产生费用；启动时即时规划单独控制
daily_planning_enabled = true
daily_planning_time = "08:00"
generate_on_start = false
```

指定模型时设置 `mode = "model"`，并在 `model` 填入 Host `model_config.toml` 的模型 name 或 model_identifier。密钥仅放在 Host 配置中。`timeout_seconds` 默认 90 秒，`max_tokens` 默认 8192；`life_context` 可补充生活背景。非法模型内容最多重试一次，超时、网络失败或不确定结果不自动重试。

### 作息偏好

```toml
[routine]
# HH:MM 格式，仅作为模型参考，不强制入睡或醒来
preferred_sleep_time = "01:00"
preferred_wake_time = "08:00"
```

## 自 v0.5.0 以来的修改

- v0.6.1：生活管理权限统一来自 MaiIdentity，移除 Vita 无效权限开关。
- v0.6.0：退役旧规划引擎，入口只运行 life，保留兼容查询和离线导入工具。
- v0.5.x：提供默认每日规划、模型任务路由、管理预览确认、中文命令及配置界面兼容修复。

完整历史见 [UPDATE_NOTES.md](UPDATE_NOTES.md)。

## 已知限制

不支持静默回执豁免（H01）、按插件归属的频率控制（H02）和通用跨插件特权委托（H03）。公开 API 和模型 Tool 不授予管理写权限。详情见[限制](docs/LIMITATIONS.md)、[API 契约](docs/CONTRACTS.md)与[拒绝诊断](docs/DIAGNOSTICS.md)。

## 鸣谢

感谢 MaiBot 与插件 SDK，以及 xuqian13、cateyemizuki 和 DavidBlackCN 的自主规划插件工作。完整继承与署名见 [SOURCES](docs/SOURCES.md)。

## 许可

[GNU Affero General Public License v3.0](LICENSE)。
