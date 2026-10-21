# 从 Autonomous Planning v5.1.0 迁移至 MaiVita

2026-10-21 验收策略更新：本文件中的既往结果保留为历史事实。单插件不再要求独立连续运行 24 小时或 7 天；长期观察、真实平台和生态联调统一移交超级 MaiBot 核心插件联合测试。这不表示这些观察已通过。当前收口状态以 I1_INTEGRATION_READY_REPORT.md 和新版自动门禁为准，生产迁移仍未授权。

当前 I1 实现补充：配置 3 的 engine.mode 选择唯一 delegate；SDK 数据根的 life/maivita.db 与 legacy/goals.db 独立，切换 authority/ticket 绑定共享锁、源 revision 和目标配置。stage_legacy.py 必须提供同一 writer_lock，只处理已验证快照并保留待审行。switch_engine.py 离线保存一致快照并产生新配置，不修改源配置或自动激活；旧库原行不变，计划不补造实际历史。精确操作和回切步骤见 [安装](../INSTALL.md)、[回滚](../ROLLBACK.md)。以下历史映射和保护要求继续适用，不能用票据绕过数据审查或生产授权。

文档基线：v0.9 · 日期：2026-10-21 · 状态：迁移设计，本文编写不执行生产迁移。

目标行为见 [MAIVITA_DESIGN_SPEC.md](MAIVITA_DESIGN_SPEC.md)，模型/表/事务见 [ARCHITECTURE.md](ARCHITECTURE.md)，实施与验收见 [DEVELOPMENT_PLAN_R0-R4.md](DEVELOPMENT_PLAN_R0-R4.md)。文中步骤和命令名称是待实现能力，不能视为仓库已经提供的 CLI。

## 1. 基线和适用范围

2026-10-21 只读检查本机 `D:\Dev\maimai-plugin\maibot-autonomous-planning-plugin`：manifest 与包版本均为 5.1.0，本地 HEAD 为 `6c936467cfde6339c9eea91bbf761e3cf0a21fdc`。未读取生产运行库或私有配置，不能假定实际部署路径、数据量与运行状态。

| 已核实项目 | 旧实现依据 | 迁移影响 |
|---|---|---|
| 插件 ID | `_manifest.json`：`davidblackcn.maibot-autonomous-planning-plugin` | ID 变化影响权限、配置归属、API 和目录 |
| 配置版本 | `config_models.py`：`SUPPORTED_CONFIG_VERSION="5.1.0"` | 新配置不能直接送入旧 normalizer |
| 数据库路径 | `plugin.py:on_load` 使用插件根目录 `data/goals.db` | 不能假定已使用 SDK 持久目录 |
| Schema | `database/goal_db.py`：goals + metadata，`SCHEMA_VERSION=1` | 新库独立版本空间，不沿用旧数字语义 |
| 旧备份 | `_create_backup()` 直接复制主 DB 至 `.db.bak` | 不足以保证含活动 WAL 的一致备份 |
| 固定睡眠 | `is_sleeping_now()` 根据配置窗口与 no_sleep_mode 判断 | 旧计划睡眠不等于实际 SleepSession |
| 未来约定 | `goal_type=pending_commitment`，存 parameters | 必须先于普通日程分类 |
| 约定消费 | `consume_pending_commitments()` 设置 completed | completed 不等于 fulfilled |
| 日程约定标记 | `parameters.is_commitment` 与 `commitment_*` | 需要关联恢复，旧匹配可能仅按近似时间/标题 |
| 当前活动 API | `get_current_activity` version=1 public | 保持返回形状并解决旧命名空间路由 |
| Hook | planner/replyer before_request、send_service.before_send | 防重复注入、静默与外部策略冲突 |
| 批量日程写入 | GoalManager 逐行事务并补偿删除 | 新状态引擎应使用单事务，不照搬补偿模型 |

旧 `metadata.schema_version` 会在初始化时被写回，迁移读取时必须绕过旧 GoalDatabase 构造器，以只读连接检查真实结构。不能把版本值当作唯一可信结构证据。

本方案直接支持已验证的 5.1.0 数据形态。更旧版本、未知字段或手工改库必须检测、报告并隔离；不猜测丢弃。cateyemizuki fork 的具体差异及当前提交必须在 R0 固定版本后检查，本次文档不宣称已合并或验证其最新修复。

## 2. 标识、目录与阶段策略

| 对象 | 旧值 | 目标值/规则 |
|---|---|---|
| 仓库/目录名 | maibot-autonomous-planning-plugin | maibot-maivita-plugin |
| Manifest ID | davidblackcn.maibot-autonomous-planning-plugin | davidblackcn.maibot-maivita-plugin，R0 核验 SDK 注册 |
| 展示名 | 自主规划插件 | MaiVita / 麦麦生活引擎 |
| 数据库 | data/goals.db | 持久数据根目录中的 maivita.db |
| 新配置结构版本 | 旧 5.1.0 | 独立 `plugin.config_version="1"`；后续按配置迁移递增 |
| 新 Schema | 旧 metadata=1 | 新 schema_migrations 从 1 开始，独立于插件发布版本 |
| 旧 API | 旧插件 ID + .get_current_activity | 新插件 ID 下同方法，并提供旧命名空间桥接 |

新插件发布版本由 R0 确定并在 manifest、包和发布记录同步；不得误用本文 v0.9 作为必需发布版本。许可证/作者/来源声明必须保留，基线许可证为 AGPL-3.0。

数据根目录优先采用部署版本 SDK 的受支持持久目录能力；具体属性名必须验证。若没有，使用显式配置的绝对 `storage.data_dir`。生产环境不得静默回退到源码目录写数据。测试目录与生产目录必须不同。

发现旧数据的优先级：显式指定旧路径 → 已核实宿主旧插件数据目录 → 旧插件根目录/data。存在多个候选时比较清单并要求明确选择，不能仅按修改时间猜测哪个是生产库；首次发现不存在任何旧库则视为全新安装，不创建“迁移成功”的假报告。

R0 将旧引擎在新的运行外壳和数据路径中维持原行为，路径搬迁用完整快照；R1 将新状态引擎设为唯一权威写入者，旧生成器通过适配器提交；R2 自主规划/睡眠分别切换。任一阶段只允许一个生效调度器和写入者。

## 3. 选择性吸收上游修复

R0 固定本地基线和 cateyemizuki fork 的比较提交，形成逐补丁清单：来源 commit、问题、涉及文件、是否本地已有等效修复、测试、采用/改写/暂缓理由。这里只读取和比较；不能把历史“8 commits behind”当作合并计划。

| 类别 | 可以吸收的目标 | 必须守住的边界 |
|---|---|---|
| SDK | 修正导入、调用参数、生命周期和配置兼容 | 不降级现有模型绑定/超时判断；以实际部署 SDK 测试 |
| Hook | 合并上下文、保留其他插件输入、注入幂等、回复阶段支持 | 不通过重写整段 messages/extra_prompt 丢弃其他插件内容 |
| 权限 | 声明实际能力、可靠来源上下文、越权拒绝 | 不用“所有用户允许”伪装 Owner；身份不代替策略 |
| 数据目录 | 持久目录适配、日志/缓存路径分离、升级保留 | 不直接移动唯一原库、不跟踪生产数据进 Git |

保留本地 v5.1.0 的指定模型路由、生成协调、最终应用校验、跨午夜、公开睡眠文案及不确定 RPC 超时不盲重试等有效修复。R0 不顺便移除固定睡眠行为；其替换在 R2 有独立验收。

## 4. 迁移控制流程

迁移状态记录为 `discovered → backed_up → staged → validated → activated`，失败标 `failed`；回退另标 `rolled_back`。每次运行使用独立 migration_run_id，记录源库指纹、路径、配置哈希、行数、工具版本和时间。

### 4.1 预检与 dry-run

1. 读取目标目录、已有配置、数据库及插件运行状态，列出可能的旧/新路径。
2. 检查宿主/SDK、磁盘空间、写权限、旧新 ID 冲突与 API 使用方。未经确认部署不能因文档存在就启动插件。
3. 用 SQLite 只读方式检查 sqlite_master、table_info、metadata、integrity_check；不调用会建表/备份/改版本的旧类。
4. 读取配置副本，识别版本、时区、固定睡眠、权限与流范围，不记录密钥到报告。
5. 在备份副本上 dry-run 分类与新库构建。输出逐类行数、未知行、时间歧义、约定冲突、配置变化和 API 消费者清单。
6. 有关键约定歧义、库损坏、无法证明来源或路由不可用时阻止正式切换；继续完成不依赖该问题的检查。

dry-run 不应写入源目录或激活新插件；可写指定临时目录及报告。运行中的 dry-run 快照会过时，正式切换仍须停止写入并重新取最终快照。

### 4.2 一致备份

采用 SQLite backup API 获取一致快照，或在受控停机、连接关闭且 WAL 已正确处理后备份完整数据库状态。不得仅拷贝正在运行的 `.db` 主文件，不得任意删除 `-wal/-shm`。

同时保存有效配置、manifest/代码版本标识、API 消费者清单、运行模式、权限与频率调整归属信息。原始配置可能含私密信息，备份保持原权限并避免公开日志。

备份目录用唯一运行 ID，禁止覆盖唯一 `.bak`。生成文件大小、SHA-256、表行数和备份清单；在快照上运行 integrity_check。任何备份失败都阻止激活。备份保留原样，后续演练从其副本恢复。

### 4.3 暂存、验证与激活

1. 停止旧生成、清理、主动行为和发送/频率 Hook，等待在途事务结束；撤销仅属于旧插件的频率调整。
2. 取最终一致快照，复核它与 dry-run 的差异，对新增约定重新分类。
3. 在目标持久目录的独立暂存库创建新 Schema，执行确定性导入；原 goals 表和源文件不动。
4. 新库中保存每一条旧行原始载荷及转换结果。转换脚本用事务，失败回滚暂存结果。
5. 运行完整性、外键、计数守恒、ID 映射、未来约定、时间及幂等校验。配置产物单独验证，运行 smoke test 时禁用对外主动发送。
6. 写入包含新数据路径/配置版本/运行模式的切换清单。Windows 下先关闭所有数据库连接，再采用同卷安全替换和启动检查；不得假定跨卷 rename 原子。
7. 激活新实例，检查只有一个写入者，读取状态、下一计时器、API 兼容与权限。数据库和配置不是一个文件事务，激活以清单状态机控制；中途崩溃启动时先恢复切换流程，不猜测哪个路径生效。
8. 记录切换水位 T0、源/目标哈希、验收结果和回滚入口，随后才启用计划生成及事件消费者。

暂存库验证失败时保留报告和坏数据样本供诊断，不替换生产库。旧库始终可从备份恢复。

## 5. 数据字段与语义映射

### 5.1 原始行守恒

旧 goals 字段包括 `goal_id, name, description, goal_type, priority, creator_id, chat_id, status, created_at, deadline, interval_seconds, conditions, parameters, progress, last_executed_at, execution_count, updated_at`。conditions/parameters 是 JSON 文本。

每个源行必须存入 `legacy_import_records`，即使 JSON 损坏也保存原始文本。唯一来源键使用稳定 `source_database_id + table + source_primary_key`，另外存源行哈希。相同运行重试返回已有结果；同键内容不同报告冲突，不静默覆盖。不同快照由 migration_run_id 关联，数据库身份不能随每次全库文件哈希变化而变化。

源行有一个主分类：约定、日程、长期目标、归档或待审；一行可以派生多个目标实体（如日程 + 约定），所有映射记录在 `legacy_id_map`。计数守恒按源行而非目标表总行数：`源行数 = 已分类源行数 + 待审源行数`，且所有源主键恰好覆盖一次。不得按名称或时间盲目去重。

### 5.2 分类顺序

| 优先顺序 | 旧数据形态 | 新去向与规则 |
|---|---|---|
| 1 | goal_type=pending_commitment | commitments，解释专用字段及旧消费语义 |
| 2 | parameters.is_commitment=true | 日程项 + 约定关联候选；无法证实关联则 needs_review |
| 3 | parameters.time_window 或 conditions.time_window | schedule_items，保持为计划，非实际 Activity |
| 4 | 明确长期目标，无日程窗口 | life_goals，保留状态、进度与来源证据 |
| 5 | 历史/系统记录、未知类型或损坏字段 | 原始归档/needs_review，不自动丢弃或执行 |

旧 creator_id/chat_id 作为来源和可见范围保存，不把每个旧 chat_id 建成独立角色。不同聊天流中冲突的旧计划不会同时成为全局实际活动；只导入为待协调计划，并保护私密约定内容。

普通长期目标的 active/paused/completed/cancelled 可保持为目标状态，但注明它们是旧系统记录，不升级成实际活动证据。普通日程的 completed 只保留 legacy_status 并作为历史计划归档/superseded，除非有独立完成证据，不设为新版 done。cancelled 约定保留取消状态；paused 约定转 needs_review，不能自动重新激活；旧状态值未知的记录统一待审。来源只有 global、无法还原私密范围时，详细约定默认 restricted，跨流只显示忙闲摘要。

### 5.3 时间映射

1. 先确认旧 `schedule.timezone`。带偏移的 created_at/deadline 按实际偏移解析；无偏移时只在旧时区明确时解释，并记录假定来源，否则待审。
2. 日程的日期优先采用可验证的显式日期；本机 v5.1.0 的查询按 `created_at` 本地日期筛选，所以无显式日期时按此行为转换并标记日期来源。发现“次日计划”与创建日期冲突时不能从标题自由猜测，必须报告。
3. `parameters.time_window` 优先，conditions 中同名字段仅作回退。两者同时存在且不同则待审。
4. 分钟数组 `[1380,1500]` 表示当日 23:00 至次日 01:00，不把 1500 直接取模丢失日期。结束值恰为 1440 表示次日 00:00。
5. 结束分钟小于开始、非数值、非法大跨度等情况不得擅自补一天；只有已核实的旧解析语义和来源能支持规范化，否则待审。原始字串另存。
6. 由 time_slot/duration_hours 转换的导出格式要验证非负与精度，按分钟统一，记录舍入策略；数据库导入优先用既有 time_window。

迁移不调用 LLM 解释日期或决定保留哪条记录，确保可重放。

### 5.4 约定优先保护

pending_commitment 的参数为 `commitment_date`、可空 `time`、`notes`、`reason`、`source`。active 且语义明确的未来记录迁移为 confirmed；时间为空则为日期级约定，不填 00:00。

旧 completed 可能表示已被日程生成器“消费”。因此：未来日期的 completed 记录仍须保护；有可核验日程关联时恢复为 confirmed 并关联计划，无明确履行证据不能设 fulfilled。过去记录缺履行证据则保存 legacy_status 并设 needs_review/归档未核实状态，不凭完成标记宣称履约。

`is_commitment` 的附加字段包括 `commitment_title/notes/reason/time`，旧算法可能按 30 分钟内时间或标题子串匹配，并没有可靠新旧 ID 链接。迁移仅将完全可证实的对应关系自动关联；模糊匹配列为候选，保留两条源记录、阻止重复接受/重复通知，不直接合并丢行。

未来未履行约定及任何 `needs_review` 约定不得被清理任务删除；涉及时间或内容冲突的关键待审项必须在启用自主写入前解决。迁移中不向约定对象发送消息。

### 5.5 实际状态与睡眠初始化

旧 goals 中的 progress、execution_count、last_executed_at 可保留为旧执行信号，不能单独证明完整实际活动历史。固定窗口睡眠由配置计算，`activity_state=sleeping`、`system_activity/locked` 也不是真实入睡记录。

- R1 `legacy_fixed`：仅从切换时刻开始记录可观察的旧行为投影，标记 `legacy_observed`。若切换时处于窗口内，可建立仅从 T0 开始的兼容睡眠会话，结束于旧窗口终点；不得补写窗口开始至 T0 的实际睡眠。
- R2 自主切换：优先在清醒且无开放会话时切模式。已有可靠的兼容会话则让其按原 deadline 结束后切换；不得为了切模式无故重置会话或重新计算整晚时长。
- 直接从旧版切到 R2：缺少可信实际状态时创建 `provenance=bootstrap_unknown` 的清醒/活动未知状态并请求首次评估；不能宣称“刚睡了八小时”。存在明确当下睡眠的可验证快照时可以转为有界兼容恢复会话，标记例外来源。

Energy/Fatigue/Stress 使用带 `estimated` 来源的初始化值或未知；睡眠债只计算已知片段并标覆盖不足。生成习惯必须等待实际经历，不从旧计划直接学习。

## 6. 配置迁移

新建配置文件并输出逐字段转换报告：原路径、原值摘要、新路径、新值、保留/转换/停用/待审及原因。旧文件不覆盖，未知字段进入报告和原始备份。配置 Schema 为 1，插件版本独立。

| v5.1.0 旧路径 | 新路径/处理 | 语义变化 |
|---|---|---|
| plugin.enabled | plugin.enabled | 保留 |
| plugin.config_version | plugin.config_version="1" | 通过迁移器转换，不手工伪造版本 |
| schedule.timezone | character.timezone | 保留并验证 IANA 时区 |
| schedule.custom_prompt | character.life_context | 生活阶段参考，不当作高权限指令 |
| schedule.sleep_start_time/end_time | routine.preferred_sleep_time/preferred_wake_time | 自主模式仅参考 |
| 同上固定窗口 | compatibility.legacy_sleep_start_time/legacy_sleep_end_time | R0/R1 legacy_fixed 保留原行为 |
| schedule.no_sleep_mode | sleep.mode=disabled（true） | false 在 R0/R1 为 legacy_fixed，R2 切 autonomous |
| schedule.auto_schedule_enabled/time | planning.daily_enabled/daily_trigger_time | 定时生成计划，不定时强制睡眠 |
| schedule.auto_generate | planning.generate_on_request | 显式写请求才生成；只读 API 不触发 |
| schedule.auto_infer_next_day_prompt | planning.direction_summary_enabled | 保留意图；用独立低频任务，R2 落地 |
| schedule.infer_time/lookback_days/max_prompt_chars/use_completion_signal | planning.direction_* | 保留原限制，完成信号标明可信度 |
| schedule.min_activities | 停用硬下限，保留迁移记录 | 不再为凑数量填满日程 |
| schedule.max_activities | planning.max_daily_items | 保留数量上限防输出失控 |
| schedule.cross_day_activity | 原值归档，新模型始终正确处理跨日 | 不再允许把历史跨日截断 |
| schedule.use_multi_round/max_rounds/quality_threshold | planning.validation.* | 保留修正机制；质量评分移除全天覆盖奖励 |
| schedule.model.* | models.strategic.*；models.immediate 默认继承 | 保留 task/model 路由与具体模型绑定 |
| schedule.max_tokens/generation_timeout/max_retries/retry_backoff | models.strategic.* | immediate 使用单独预算；不扩大不确定重试 |
| schedule.recent_schedule_days | planning.history_days | 历史区分计划与实际 |
| schedule.history_message_limit/knowledge_search_limit | planning.context.* | 按可见范围读取；不跨群拼全文 |
| schedule.inject_schedule/inject_into_replyer/max_future_activities | integration.injection.* | 保留 Hook 开关和未来项数量 |
| inject.* | integration.injection.* | 保留逐流优化，缓存含状态版本 |
| schedule.enable_detailed_description/min_description_length/max_description_length | planning.presentation.* | 展示偏好，非事实校验 |
| schedule.role_judge_enabled/temperature | planning.request_judge.* | 人设意愿判断不授予管理权限 |
| schedule.admin_users | admin.legacy_admin_users | 显式列表保留，空列表旧语义必须待审 |
| schedule.allowed_streams | integration.allowed_streams | 注入/命令范围，不创造分流生活状态 |
| schedule.proactive_streams/enable_proactive_trigger | compatibility.proactive.*，后转 integration.social.* | R0/R1 保留，R3 外部消费者接管 |
| schedule.frequency.* | compatibility.frequency.*，后转策略适配建议 | 不让旧新系数叠加 |
| schedule.cache_ttl/cache_max_size | runtime.cache.* | 不以 TTL 延迟睡眠事实刷新 |
| schedule.llm_log_enabled/retention_days | observability.model_logs.* | 保留意愿但增加脱敏和访问限制 |
| autonomous_planning.cleanup_interval/cleanup_old_goals_days | storage.cleanup_interval/history_detail_days | 新清理保护未完约定、原始迁移数据和未投递事件 |

旧 admin_users 空列表表示所有用户可执行命令。不能无提示转换成全体 Owner，也不能无提示取消既有权限：R0/R1 在 compatibility 中显式保持并给出迁移诊断；R2 开启特权写接口前，必须确定非空可信管理员配置或接入身份/策略服务。只读查询不受此阻塞。

未知/删除字段（如旧版已拒绝的 sync_sleep_frequency、sleep_talk_adjust、llm_task_name 等）原样留在报告，不重新激活。新安装默认 autonomous，R0/R1 过渡发行默认 legacy；发布资料必须注明阶段。

## 7. API、Tool、命令与 Hook 兼容

### 7.1 旧当前活动 API

完整旧路由为 `davidblackcn.maibot-autonomous-planning-plugin.get_current_activity`，参数 `chat_id="global"`、额外 kwargs 被忽略。新 ID 下保留同名方法，成功返回以下旧结构：

```json
{
  "has_activity": true,
  "activity": {
    "name": "睡觉",
    "description": "",
    "goal_type": "rest",
    "time_window": "00:10-08:10",
    "activity_state": "sleeping"
  },
  "activity_state": "sleeping",
  "next_activities": [],
  "as_of": "2026-10-21T00:20:00+08:00",
  "timezone": "Asia/Shanghai",
  "error": null
}
```

保持 has_activity、activity、next_activities、as_of、timezone、error 及活动字段类型；可选字段只能增量增加。sleeping 之外阶段在旧 activity_state 投影为 awake，不强迫旧消费者理解新枚举。没有已知时间窗口则用 null；不能为灵活活动伪造精确起止。未初始化返回旧 `plugin_not_initialized` 形状。

新版 `chat_id` 仅影响可见范围，生活事实始终全局。私密活动需要脱敏名称和描述；这是明确的兼容语义变化。跨日 time_window 旧字符串保留格式，精确日期请使用新 API，不在旧字符串中暗藏新协议。

### 7.2 旧命名空间不能凭重命名保留

R0 验证宿主是否支持旧 API alias。若支持，使用受支持别名转发到新实现；若不支持，使用最小旧 ID 兼容桥接插件，只注册转发 API，无数据库、生成器、睡眠循环、发送 Hook 或调频功能。桥接插件与完整旧插件不可同时启用。

迁移报告必须列出消费者及验证结果。宿主既不支持 alias、又无法安全部署桥接时，更新已知消费者至新路由后才切换，报告尚未迁移的调用方；不得宣称“方法同名所以旧 API 可用”。

### 7.3 其他入口

旧 Tool 名称为 `manage_goal_v4`、`get_planning_status_v4`、`generate_schedule_v4`、`apply_schedule_v4`，命令为 `/plan`、`/规划`。R0/R1 保留公开参数及回执形状，新增清晰诊断。

R2 起写入口统一映射到新命令：generate 生成提案后校验；apply 转为 ApplyPlanPatch；manage 的更新/取消/删除必须区分目标、计划和约定并检查权限。旧删除请求不得物理删除实际历史或未履行约定。无法无损兼容的操作返回明确 unsupported/error，不假装成功。

R0 对所有入口提取参数/返回 fixtures，形成逐项兼容清单。Python 内部 import 路径不视为公共 API；若发现真实依赖，应纳入清单而非盲目保持全部旧内部模块。

旧 Hook 的融合、命令回执许可和频率调整按架构文档执行。R3 交接策略时先撤销旧调整再启用外部策略，并验证热重载/卸载也不留下静默。

兼容层至少贯穿 R0–R4，默认开启。退役需完成消费者清单、调用统计、公开迁移说明和至少一个明确发布窗口；日志限频，不在每条聊天提示弃用。

## 8. 回滚策略

### 8.1 三种回退不能混为一谈

| 类型 | 操作 | 数据语义 |
|---|---|---|
| 激活前失败 | 丢弃/保留失败暂存供诊断，继续旧实例 | 源库未改，无业务数据回退 |
| 新引擎内部功能回退 | 停止自主决策，保留新版状态和数据库；按明确策略继续基础计时 | 不需要降级 Schema，不能把已有会话交给不认识它的旧引擎 |
| 切回旧 v5.1.0 程序 | 停新写入，恢复 T0 旧程序、旧配置、旧库和旧路由 | 恢复的是切换点；新产生的生活数据不能自动无损降级 |

### 8.2 正式回滚步骤

1. 禁止新写请求、停止模型任务与事件对外发送，关闭新调度器；保存当前 maivita.db 一致快照和失败诊断。
2. 导出 T0 之后新增/修改的约定、取消记录、目标和配置差异，附旧 ID 映射。尤其保护回滚后仍将到期的约定。
3. 明确回滚恢复点与差异处理：纯技术演练可恢复 T0；真实运行后回滚须先核对新约定及取消事项，避免恢复后遗忘新约定或重新执行已取消旧约定。
4. 从已验证备份的副本恢复旧库、旧配置及对应旧代码版本；不要把 maivita.db 改名 goals.db，不在新库执行猜测性 down migration。
5. 撤销新插件的频率调整、Hook、路由别名/桥接冲突和运行锁。仅恢复旧插件自身所需的能力授权。
6. 验证旧库 integrity_check、配置版本和旧 API；在主动发送禁用条件下 smoke test，再恢复业务。
7. 保留新库和差异清单，不覆盖备份；记录 rolled_back、操作者、时间和遗留待对账项。

R4 的习惯、副活动、完整睡眠片段等在旧 Schema 无法表达，只能保存在新版归档和差异导出中。不能承诺回滚后旧版继续保有全部新行为。数据丢失窗口以实际 T0 及差异清单衡量，不用“可回滚”掩盖已产生的新记录。

已发出的消息是不可回滚的外部副作用；迁移和回滚不得自动重放历史事件发送。恢复旧计划中的已取消约定前必须对账。

## 9. 验证矩阵与验收产物

| 场景 | 期望 |
|---|---|
| 空库/无旧库 | 生成明确新安装报告，不伪造历史 |
| 正常 goals + metadata | 源行完全可追溯，读写新库正常 |
| 活跃 WAL | 备份包含已提交数据，恢复完整性通过 |
| 损坏 JSON/未知字段/未知版本 | 保存原始行，待审或阻止激活，不清库 |
| 跨午夜、24:00、无时区、DST | 正确转换或明确待审，无静默截断 |
| active/completed pending_commitment | 不把旧消费标记当履约；未来约定保留 |
| is_commitment 近似关联 | 不错误合并，报告歧义 |
| 同库重复导入/中断重跑 | 新实体、事件和约定不重复 |
| 两个旧目录/新目录非空 | 阻止覆盖和猜测性选择 |
| 导入后校验失败/磁盘满/切换中崩溃 | 不激活半成品，可恢复到唯一数据源 |
| 旧 API alias 或 bridge | 旧全路由真实可调用，同状态且脱敏 |
| 产生新约定后回滚 | T0 后差异留存并对账，无重复主动通知 |

必须交付：预检报告、源/目标文件清单与校验值、配置字段映射、逐源行迁移计数、待审记录、API 消费者矩阵、切换清单、回滚演练记录。只有这些结果通过后才进入正式激活。具体测试编号和阶段门禁见开发计划。
