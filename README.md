# maibot-maivita-plugin

MaiVita 是一个 MaiBot 插件，为麦麦提供自主日程规划、当前活动感知和固定睡眠能力。插件根据角色人设、近期聊天、历史日程和已确认约定生成生活安排，并向 planner、replyer 提供角色当前正在做什么的上下文。

- 当前版本：`0.1.0`
- MaiBot：`1.2.3–1.99.99`（Manifest 声明范围）
- Plugin SDK：`2.15.0–2.99.99`（Manifest 声明范围）
- 配置版本：`1`
- 项目地址：[DavidBlackCN/maibot-maivita-plugin](https://github.com/DavidBlackCN/maibot-maivita-plugin)

声明范围不代表其中所有版本均已完成兼容确认。当前入口保留日程与固定睡眠，自主睡眠和混合规划尚未接入。可信管理回执、频率归属撤销和 Owner Auth 身份接口未接通，相应能力拒绝例外发送、停用调频或拒绝身份授权。当前不支持直接接管已有生产实例。

## 功能

### 自主日程生成

- 使用 MaiBot 模型路由生成符合角色人设、兴趣和生活阶段的日程。
- 将近期聊天、历史日程、知识库与已确认约定分层提供给模型。
- 支持定时生成、手动重新生成和临时附加自然语言要求。
- 允许休息、通勤和自由时间，不强制填满全天。
- 使用统一生成协调器，避免命令、Tool 和自动调度重复生成。

### 程序级固定睡眠

实际睡眠窗口由 `compatibility.legacy_sleep_start_time` 和 `legacy_sleep_end_time` 指定：

```toml
[sleep]
mode = "legacy_fixed"

[compatibility]
legacy_sleep_start_time = "01:00"
legacy_sleep_end_time = "08:00"
```

睡眠活动由程序锁定时间和内部标记，自然描述由模型生成。缺失、重复、时间非法或与普通活动冲突的日程会被修正或拒绝应用；支持 `23:00–07:00` 等跨午夜窗口。时间起点为 `00:00–23:59`，区间终点额外支持 `24:00`。

`routine.preferred_sleep_time` / `preferred_wake_time` 不改变当前入口的固定窗口。关闭固定睡眠使用 `sleep.mode = "disabled"`。

### 严格校验与安全落库

```text
上下文构建 → 模型生成 → 解析与归一化 → 固定睡眠约束 → 日程校验 → 应用前检查 → 批量落库
```

非法时间、活动重叠、睡眠冲突和约定丢失等错误会阻止应用。批量创建失败时补偿回滚，避免留下半份日程。配置不兼容、数据目录不明确、已有旧插件写入或写入锁被占用时拒绝启动。

### 当前活动与睡眠静默

- 向 `maisaka.planner.before_request` 和 `maisaka.replyer.before_request` 注入当前活动。
- 提供当前活动插件 API，返回活动、后续安排与 `sleeping/awake` 状态。
- 睡眠期间暂停本插件的主动消息，并通过 `send_service.before_send` 阻断模型出站消息。
- 缺少可信发送回执时，命令回复不享有睡眠静默例外。
- 缺少按插件归属撤销的频率接口时，不修改 Host 全局频率值；配置中的频率系数不能视为已生效。

### 人设更新与兼容路由

角色配置更新后重新读取 bot profile，后续日程使用新的昵称、人设和兴趣。新旧完整当前活动 API 路由保留；不要同时启用旧完整规划插件。

## 安装

### 1. 放置插件

将仓库克隆到 MaiBot 的插件目录：

```bash
git clone https://github.com/DavidBlackCN/maibot-maivita-plugin.git
```

```text
MaiBot/
└── plugins/
    └── maibot-maivita-plugin/
        ├── _manifest.json
        ├── plugin.py
        ├── config_models.py
        └── config.example.toml
```

停止旧完整规划插件，使用独立实例和空数据目录首次安装；不要从旧实例复制运行数据库或凭据。

### 2. 创建配置

根据 [`config.example.toml`](config.example.toml) 创建新的 `config.toml`，保留 `config_version = "1"`。启用前至少填写共享写入锁路径和管理员 ID：

```toml
[plugin]
enabled = true
config_version = "1"

[admin]
legacy_admin_users = ["your-user-id"]

[storage]
data_dir = ""
writer_lock = "D:/MaiBot/instance-locks/maivita.lock"
```

`writer_lock` 必须是绝对路径；同一部署的所有 MaiVita 代码或数据目录共用此路径。持久化目录优先使用宿主注入的绝对 `ctx.paths.data_dir`，否则必须显式配置绝对 `storage.data_dir`。没有明确数据目录就拒绝启动，不回退到源码目录。

管理员列表为空时，默认拒绝 `/plan` 命令。`compatibility.legacy_empty_admin_all` 仅保留旧配置的开放命令语义，不代表 Owner 身份授权。

### 3. 检查模型并启动

默认使用 MaiBot 的 `replyer` 模型任务：

```toml
[models.strategic]
mode = "task"
task = "replyer"
model = ""
```

模型提供方和密钥由 Host 的 `model_config.toml` 管理，不写入插件配置。确认宿主已授权 Manifest 所需能力，再启动 MaiBot。数据库、模型日志和图片位于持久化数据目录中的 `goals.db`、`llm_logs/`、`images/`。

### 从旧版本更新

先停用旧实例并备份旧配置与数据库，再使用新版示例重建配置。不能只改配置版本号，也不能用旧配置整体覆盖新配置。仅 `5.1.0` 格式可用转换器处理明确指定的配置副本：

```bash
python -m pip install -r tools/requirements.txt
python tools/convert_config.py old-config-copy.toml new-config-copy.toml
```

转换器拒绝未知字段、不覆盖已有目标、不更改源文件；结果仍需检查数据目录、共享写入锁和权限。转换配置不等于迁移数据库或启用新引擎。

## 命令

命令入口支持 `/plan` 和 `/规划`，子命令使用英文，受管理员配置和会话范围约束。

| 命令 | 说明 |
|---|---|
| `/plan`、`/plan help` | 显示帮助 |
| `/plan status` | 查看今日日程 |
| `/plan list` | 图片展示日程，失败时回退文字 |
| `/plan frequency`、`/plan freq` | 查看频率配置与 Host 当前值；不表示拥有调频权限 |
| `/plan regenerate` | 后台重新生成今日日程，立即返回任务信息 |
| `/plan regenerate <额外要求>` | 为本次生成附加自然语言要求 |
| `/plan delete <序号或 goal_id>` | 删除指定目标 |
| `/plan clear [days]` | 清理旧日程，默认保留今天 |

也提供日程生成、应用、状态查询、目标管理与日程更新 Tool。命令与 Tool 共用校验、生成互斥和持久化逻辑。

## 配置

完整字段和默认值见 [`config.example.toml`](config.example.toml)，配置校验由 `config_models.py` 声明。

| 配置 | 说明 |
|---|---|
| `character.timezone` | IANA 时区，例如 `Asia/Shanghai` |
| `character.life_context` | 角色生活阶段和长期背景 |
| `planning.mode` | 当前仅接受 `legacy` |
| `planning.daily_enabled` / `daily_trigger_time` | 自动生成开关和每日触发时间 |
| `planning.history_days` | 日程历史回看天数 |
| `planning.max_daily_items` | 每日日程条数上限 |
| `models.generation_timeout` | 模型调用超时秒数 |
| `models.generation_max_retries` | 明确 Provider 错误时的有限重试次数；能力 RPC 超时不自动重试 |
| `models.strategic` | 模型任务路由或指定模型 |
| `integration.allowed_streams` | 注入与命令会话范围，留空不限制 |
| `integration.inject_schedule` / `inject_into_replyer` | planner / replyer 注入开关 |
| `admin.legacy_admin_users` | 命令管理员 ID 列表，留空默认拒绝 |
| `storage.writer_lock` | 同一部署共用的绝对写入锁路径 |
| `storage.data_dir` | 无宿主数据目录时的绝对备用目录 |
| `observability.model_logs_enabled` | 模型日志开关 |

指定具体模型时，使用 `mode = "model"` 和 Host 配置中的模型名称。此模式依赖 Host 内部模型接口，兼容性受宿主版本影响；任务路由仍为默认方式。

## 公共 API

| 完整路由 | 说明 |
|---|---|
| `davidblackcn.maibot-maivita-plugin.get_current_activity` | 当前活动与后续安排 |
| `davidblackcn.maibot-autonomous-planning-plugin.get_current_activity` | 保留旧消费者兼容路由 |

两个路由读取同一引擎。旧路由兼容不要求同时安装旧完整规划插件；启用双写入者会被拒绝。

## 备份与恢复

工具只读取显式指定的路径，没有默认业务数据路径：

```bash
python tools/r0_migrate.py dry-run --source /absolute/source/goals.db --output /absolute/snapshot/goals.db
python tools/r0_migrate.py backup --source /absolute/source/goals.db --output /absolute/backup/goals.db
python tools/r0_migrate.py restore --source /absolute/backup/goals.db --sha256 <SHA256> --output /absolute/restore/goals.db
```

目标必须是源目录之外的新文件。备份采用 SQLite 一致快照并校验结构、完整性和 SHA-256；dry-run 只预检与准备快照，不激活数据迁移。恢复到独立空目录，不覆盖运行库，不拼接 WAL/SHM 文件。

回退前先停止当前实例并保留新增记录，再使用匹配的旧程序、旧配置和已核验旧库。不要让新旧调度器并行，也不要把不同 Schema 的数据库改名后交给旧程序。

## 更新记录

版本变化见 [CHANGELOG](docs/CHANGELOG.md)，来源与继承关系见 [SOURCES](docs/SOURCES.md)。

## 鸣谢

- [MaiBot](https://github.com/Mai-with-u/MaiBot) 及其插件 SDK。
- [DavidBlackCN/maibot-autonomous-planning-plugin](https://github.com/DavidBlackCN/maibot-autonomous-planning-plugin)，MaiVita 的直接代码基线。
- [xuqian13/autonomous_planning_plugin](https://github.com/xuqian13/autonomous_planning_plugin)，原作者靓仔 / xuqian13 及历次贡献者。
- [cateyemizuki/autonomous_planning_plugin](https://github.com/cateyemizuki/autonomous_planning_plugin)，SDK、Hook 与数据目录修复参考。

## 许可

[GNU Affero General Public License v3.0](LICENSE)
