# MaiVita

为 MaiBot 提供持久的生活状态、活动、计划、目标与睡眠，让日常回复感知角色正在做什么。

- 当前版本：`0.9.0`
- MaiBot 版本范围：`1.3.12–1.3.18`
- Plugin SDK 版本范围：`2.15.0–2.19.0`
- 项目地址：[MaiVita](https://github.com/DavidBlackCN/maibot-maivita-plugin)

仅支持精确配对 **Host 1.3.12 / SDK 2.15.0**、**Host 1.3.15 / SDK 2.16.0**、**Host 1.3.18 / SDK 2.19.0**，不支持中间版本或交叉配对。无需私有 Host 补丁。

## 功能

### 日程规划与生活状态

根据 Host 的昵称、人设、行为和回复风格生成每日计划，向 Planner 和 Replyer 提供公开生活上下文。计划代表打算做的事，活动历史代表实际发生的事；活动段结束不证明目标完成；不能把计划直接当成经历。长期目标自动经营、习惯学习和承诺自动协商暂未启用。

每日规划默认开启，使用 Host 的 `planner` 模型任务，**会产生模型调用费用**。按角色时区每天 08:00 触发，晚启动会补触发当天规划；重启不会重复已调用的规划。参考规划与滚动生活共用角色时区自然日预算，新安装默认 20 次，包含失败重试。实际活动只有滚动决策一条推进路径。

完整一天通常包含 10–16 项参考安排，睡眠、吃饭、洗漱也计入。按有意义的生活阶段划分，允许稳定重复、空档和跨午夜睡眠；数量不是硬性要求。晚间启动或重新生成时主要安排剩余时间，不补写过去。计划描述打算，不提前写死购买结果、学习收获或他人的行为。

普通聊天会参考当前公开活动、地点、心情和最近 24 小时的已记录生活，午夜不会清空衔接。背景仅在相关或被问到时自然带出，不逐条播报，也不额外调用摘要模型；私有记录和观察缺口中的经历不进入聊天素材。生活记录是角色模拟，不代表实际执行了外部操作。

### 可确认的生活管理

管理员可以新增或调整计划、管理目标、开始和结束活动、请求睡眠。新增管理操作先预览，再由同一账号在同一会话两分钟内确认；状态或权限发生变化时，旧预览会被拒绝。确认结果持久保存，重复确认不会重复修改。

### 睡眠与静音

睡眠时普通出站消息会被守卫拦截，包括命令回执。MaiHush 的硬静音独立生效：唤醒不会解除硬静音，解除硬静音也不会唤醒。作息配置是模型偏好，不是强制睡眠时刻。

身体极值拦截默认关闭，可通过 `[availability] enabled = true` 开启。学习、工作、出行、创作显示为“正在专注”（API 值 `slow`），聊天背景提示自然简短回应，仍由 MaiBot 正常回复，不保证延迟或限长。普通空闲为 `free`；睡眠或身体达到极值（默认疲劳 ≥ 90、精力 ≤ 10）优先推导为 `busy`。启用极值拦截后 busy 会暂停出站消息。旧活动的能耗字段仍保留读取兼容，新活动按类别判断。

忙碌期间不延迟、不缓存、不补发消息，`followup_after_busy` 仅支持 `false`。关闭忙闲守卫或 `busy_when_sleeping` 不会解除原有睡眠静默，MaiHush 仍独立决定是否拒绝发送。两个 EARLY 守卫不依赖先后顺序；任一个 abort 都终止发送，continue 也不会绕过另一个守卫。busy 时 status 命令回执同样可能被拦截；公开 availability API 可只读查看三态，未知身体值不触发极值规则。

### 可选主动表达

**MaiCoordinate 是可选扩展**：不安装也能使用 MaiVita、MaiIdentity、MaiHush 三核心功能。需要根据公开生活事件主动发言时，可另行安装 MaiCoordinate；其默认 shadow 模式不调用模型、不发送消息，主动表达须显式选择 controlled。

### 身体变化

身体数值是角色模拟估计。按实际睡眠会话或活动类别计算每小时变化，新活动不由模型填写精力速率。

| 状态／类别 | 精力 | 疲劳 | 压力 |
| --- | ---: | ---: | ---: |
| 实际睡眠 | +8 | −5 | −3 |
| 清醒休息 | +3 | −2 | −1 |
| 学习、工作、创作、出行 | −3 | +2 | +0.8 |
| 其他清醒活动 | −1 | +1 | −1 |

默认每 15 分钟检查一次，检查不一定调用模型。模型失败或预算用完时，无活动或过度疲劳者进入中性休息，其他情况有界延续；睡眠按截止时间或可信管理员唤醒结束。停机未知区间不补编经历，升级不追溯重算旧身体值。

### QQ 空间组合

Vita 提供公开生活，MaiCoordinate 判断时机，固定适配的 MaiTrace 6.3.0 执行发布与浏览互动。空间独立于群路由；新配置先观察，旧配置迁移后关闭。默认每天最多尝试发布 2 次、间隔至少 4 小时；最多浏览 6 轮、间隔至少 1 小时，每轮最多 5 条。互动须设置显式白名单。

适配补丁、推荐配置与离线构建方法见 [MaiTrace 适配说明](ecosystem/maitrace/README.md)；协调层配置见 [MaiCoordinate](ecosystem/coordinator/README.md)。QQ 登录、模型和图片服务仍由 MaiTrace 配置，不能同时启用它自身的自动生活与空间调度。

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

确认 Host 的 `model_config.toml` 已配置 `planner` 任务。若不需要每日参考计划，可关闭 `daily_planning_enabled`；滚动生活仍会调用模型。完全停用需关闭插件总开关。

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

停机备份代码、`config.toml` 和完整插件数据目录，再替换代码。保留现有数据与写锁设置；配置版本为 `"4"`，使用 `plugin`、`instance`、`model`、`routine`、`scheduler`、`availability` 六段。旧 `owner_auth_enabled` / `privileged_writes_enabled` 无论真假均被忽略，不产生权限；其他未知字段仍会报错。

不自动导入旧规划库或降级数据库。数据中的非 life 权威记录、未知 schema 和损坏库会阻止启动。备份应包含 `life/maivita.db`、`life/management.sqlite3` 与 `engine-authority.json`；恢复须使用匹配的代码、配置和完整数据，不能覆盖正在运行的数据库。详见[回滚指南](docs/ROLLBACK.md)。配置与数据转换工具分别为 `tools/phase1_config.py`、`tools/phase1_data.py`；它们输出新文件或副本，不直接覆盖运行实例。

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
| `/maivita wake`、`/maivita replan` | 管理员唤醒、重新决定当前活动 |
| `/maivita plan regenerate` | 重新生成今日参考计划；沿用 replan 权限 |

日期支持今天、明天或 `YYYY-MM-DD`，时间按角色时区。中文管理仅识别 `麦麦管理` 前缀后的有限语法，不执行普通闲聊里的指令。新目标和活动公开可见；公开命令不能编辑私密计划与目标。睡眠有时长、承诺冲突及冷却约束，唤醒后五分钟内不能开启新的睡眠会话。回执被静音拦截时，恢复后用 `result` 查询，不要重复提交。

## 配置

[config.example.toml](config.example.toml) 只列常用设置，省略字段使用默认值。WebUI 的常用区域提供会话、时区、生活背景、调用额度和作息；活动检查与休息拦截在高级区域，模型路由等可在原始 TOML 中修改。配置格式版本与插件发布版本不同。

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
# 角色时区自然日最多 20 次生活调用尝试；失败也计入
daily_budget = 20
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

版本变化见 [UPDATE_NOTES.md](UPDATE_NOTES.md)。

### 高级参数与旧配置

下列参数通常无需修改。隐藏字段仍可在原始 TOML 中设置，升级不会删除旧值；不要把示例直接覆盖到已有配置。

| 字段 | 默认值与说明 |
| --- | --- |
| `model.mode / task / model` | `task / planner / ""`；指定模型时使用 `mode = "model"` 并填写模型名 |
| `model.timeout_seconds / max_tokens` | `90 / 8192`；超时 1–300 秒，输出上限 128–16384 token |
| `model.generate_on_start` | `false`；每日规划关闭时，每次加载额外请求一次当前活动决策 |
| `scheduler.decision_interval_minutes` | `15`；定期检查并不等于调用模型 |
| `scheduler.segment_min_minutes / segment_max_minutes` | `15 / 180`；检查间隔须在两者之间 |
| `scheduler.segment_sleep_max_minutes` | `720`；睡眠硬上限，不小于活动上限且最多 1080 分钟 |
| `availability.enabled` | `false`；控制身体极值拦截，不控制睡眠静默 |
| `availability.slow_categories` | `study, work, travel, creative`；正在专注的类别 |
| `availability.busy_fatigue_threshold / busy_energy_threshold` | `90 / 10`；0–100 的模拟身体阈值 |
| `routine.initialize_body_state` | `true`；只把首次未知的身体读数初始化为中性值，已有读数不重置 |

`routine.weekly_pattern` 仅保存旧配置，尚不影响生活；可将工作日与周末偏好写入 `model.life_context`。`scheduler.enabled` 固定开启，`change_chance_percent` 固定为 0，`daily_decision_budget` 由 `model.daily_budget` 统一覆盖。`availability.followup_after_busy` 和 `instance.frequency_enabled` 只支持 false。`availability.busy_when_sleeping` 默认 true，仅影响三态推导，不解除睡眠静默。`acknowledgement`、`writer_lock` 属于兼容与部署字段，普通用户无需填写。

## 已知限制

不支持静默回执豁免（H01）、按插件归属的频率控制（H02）和通用跨插件特权委托（H03）。公开 API 和模型 Tool 不授予管理写权限。详情见[限制](docs/LIMITATIONS.md)、[API 契约](docs/CONTRACTS.md)与[拒绝诊断](docs/DIAGNOSTICS.md)。

## 鸣谢

感谢 MaiBot 与插件 SDK，以及 xuqian13、cateyemizuki 和 DavidBlackCN 的自主规划插件工作。完整继承与署名见 [SOURCES](docs/SOURCES.md)。

## 许可

[GNU Affero General Public License v3.0](LICENSE)。
