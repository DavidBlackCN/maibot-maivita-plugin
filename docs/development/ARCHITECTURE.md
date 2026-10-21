# MaiVita 架构与接口规范

文档基线：v0.14 · 2026-10-21。当前正式入口为 plugin.py → entrypoint.MaiVitaPlugin，配置版本 3，默认选择 life。以下旧版本摘要保留历史背景，当前实现以本节为准。

## I1 Integration-Ready 接线

- Runner 原生流程为注入上下文/配置 → bootstrap → 注册组件 → on_load；路由器按配置只声明并加载一个 delegate，不在热更新中替换组件。
- life 使用 SDKModelPort 与 DevelopmentRuntime，API/Tool/Command/Hook 已正式注册；legacy 保留旧固定睡眠实现，不能同时成为生活状态权威。
- SDK 授予数据根内，life/maivita.db 与 legacy/goals.db 分离；engine-authority.json 指定唯一模式和 revision，共享 writer_lock 先于数据库打开。Runtime 再锁定 life 目录。
- 切换由离线工具生成目标配置及绑定源 revision/配置摘要的票据；成功加载并再次校验后原子更新 authority。失败自清理，不提交新模式，不回退双启。
- H01 无静默发送例外、H02 无全局调频写入、H03 及统一策略/社交传输关闭。公开契约见 [CONTRACTS](../CONTRACTS.md)，规范形状独立于真实可用接口。
- 长期观察与真实生态联调移交超级 MaiBot 联合测试；本插件只以实际自动化证据裁决 Integration Ready，不接管生产。

## 历史 v0.13 架构摘要

文档基线：v0.13 · 日期：2026-10-21 · 性质：目标架构与隔离实现说明；当前 I1 增加独立开发包入口，仓库默认入口仍为 R0，生产接管未验收。

产品行为见 [MAIVITA_DESIGN_SPEC.md](MAIVITA_DESIGN_SPEC.md)，数据升级见 [MIGRATION_PLAN.md](MIGRATION_PLAN.md)，实施顺序见 [DEVELOPMENT_PLAN_R0-R4.md](DEVELOPMENT_PLAN_R0-R4.md)。本文不假定宿主已经提供下述新接口；SDK 绑定与外部契约在 R0/R3 核验。

I0 核验补记：SDK 2.15.0 与固定 Host `5c7637e` 的原始 Runner/运行时 IPC 已在合成空实例验证，API/Tool/命令/Hook 注册与明确 task_name/model_name 路由可用。完整 Host supervisor、真实 provider、平台发送及目标部署版本尚未验收。可信管理回执透传、按插件归属撤销频率和 Owner Auth 声明接口仍缺失；身份写入默认拒绝。详见 [I0 契约矩阵与方案](I0_INTEGRATION_PLAN.md)。这项证据不改变下述目标架构，不启用新旧双调度器，不新增生活模拟能力。

I1 后续：`development/plugin.py` 单独绑定新 Runtime、SDKModelPort、API/Tool/Command/Hook；白名单开发包根入口仅导入它，不导入 R0 GoalManager/服务。独立 Host 的 SDK data_dir 下使用新库，实例级锁先于建库取得，Runtime 自身再锁定库目录；只允许显式新测试实例，不自动迁移。H01 无回执例外、H02/H03 明确关闭/拒绝，H04 自清理。逻辑 LifeAPI 由该入口注册，原文“未注册”指 I0/R2–R4 历史边界，根 R0 仍不改接。模型端口只用 SDK 正式路由，严格候选与现有协调器预算；首次安装门禁、真实测试范围及限制以 [I1 报告](I1_REPORT.md) 为准，平台送达/长期观察不由本轮代替。

## 1. 架构决定

| ID | 决定 | 理由 |
|---|---|---|
| ADR-01 | 每角色单权威状态，所有写入经 Life State Engine | 防止各聊天流和各模块独立改变生活事实 |
| ADR-02 | 计划、实际活动、睡眠会话与生活事件分开存储 | 重规划不能改写历史；睡眠要独立恢复 |
| ADR-03 | LLM 生成有类型的候选命令，程序校验并提交 | 保留自主性，拒绝非法时间、权限和过期结果 |
| ADR-04 | SQLite 事务 + 乐观版本检查 + 持久化事件发件箱 | 状态成功与事件可靠发布保持一致 |
| ADR-05 | 进程内每角色协调器，数据库保证最终唯一性 | 合并并发决策，同时保护重启和重复回调 |
| ADR-06 | 基础规则核心不依赖 SDK，外部能力经适配器 | 便于回放、离线测试和宿主升级 |
| ADR-07 | 旧 API 是新权威状态的只读投影 | 保持消费者兼容，不能运行两套生活引擎 |
| ADR-08 | R0/R1 保留行为，R2 才切自主睡眠 | 隔离基础修复和行为变化的风险 |

首版支持一个宿主进程、一名角色。数据库仍携带 `character_id`，但不承诺多宿主并发运行；发现第二个写入实例应阻止启动，而非依靠 SQLite 锁临时协调两个调度器。

## 2. 模块职责与依赖

```text
宿主 Hook / 命令 / Tool / 外部请求 / 本地计时
                    |
             Integration Adapters
          身份声明 + 策略裁定 + 上下文裁剪
                    |
      Decision Coordinator（合并、预算、版本、幂等）
            /                  \
   Planning / LLM          Sleep Service
   长期、每日、即时          会话、到期、中断
            \                  /
              Life State Engine
          校验 -> 领域转换 -> 单次事务
                    |
     SQLite repositories + life_events outbox
            |                    |
       查询投影/缓存          Event Dispatcher
            |                    |
      新 API / 旧 API        策略层、主动社交、记忆适配器
```

| 模块 | 拥有的职责 | 禁止行为 |
|---|---|---|
| `life_state` | 权威快照、事实转换、主观估计、版本和领域约束 | 调用消息发送、直接识别 Owner |
| `planning` | Life Direction、每日计划、即时提议、计划差异、约定冲突分析 | 直接写状态表、按计划时间冒充实际活动 |
| `sleep` | 睡眠状态机、时长校验、deadline、中断和恢复 | 自行维护第二份 `is_sleeping` 真值、靠活动名称判睡眠 |
| `events` | 事件 Schema、outbox 投递、重试和消费者确认 | 在事务提交前对外宣布成功、自行群发消息 |
| `coordinator` | 事件合并、调用预算、任务去重、陈旧结果拒绝 | 持有数据库事务等待模型、绕过权限 |
| `continuity` | 偏好、习惯、目标、摘要和证据 | 覆盖人设硬约束、把摘要当成精确历史 |
| `storage` | 数据库连接、事务、版本迁移、快照与恢复 | 模块导入即改库、遇错删库 |
| `integrations` | SDK、Hook、身份、策略、主动行为、记忆桥接 | 侵入宿主数据库、修改其他插件配置 |
| `compatibility` | 旧 API/Tool/命令映射、旧配置转换、退役诊断 | 旧新两份状态双写、长期并行调度 |
| `clock` | UTC 时刻、角色时区、本地日边界、单调计时 | 使用机器本地时区推断角色日期 |

建议目录布局（开发时可按宿主打包要求调整，不要求 R0 一次创建全部空目录）：

```text
plugin.py / _manifest.json / config_models.py
domain/           # models, commands, invariants, time
life_state/       # engine, projections, subjective_state
planning/         # direction, daily, immediate, prompts, validation
sleep/            # state_machine, scheduler, recovery
coordinator/      # decision coordination, budgets, idempotency
continuity/       # commitments, goals, habits, summaries
events/           # schema, dispatcher, subscriptions
storage/          # repositories, unit_of_work, migrations
integrations/     # sdk, hooks, identity, policy, social, memory
compatibility/    # legacy API, tools, config, bootstrap
tests/            # domain, migration, contract, integration, replay
```

旧模型路由、解析器、Generation Coordinator、时区和公开文案过滤逻辑按职责复用。对 cateyemizuki fork 的 SDK、Hook、权限与目录补丁逐项验证后吸收；不整体覆盖本地 v5.1.0 的模型锁定、并发和跨日修复。

### 2.1 R1-01/02 初次交付边界（历史记录）

按开发计划 v0.10，R1 基础开发可与未完成的 R0 实机验收并行。`domain/clock.py`、`domain/models.py`、`domain/constraints.py` 不依赖 SDK；`storage/life_database.py`、`repositories.py`、`schema.py` 和 `migrations/001_initial.sql` 提供显式创建/版本迁移、仓储和事务工作单元。所有本轮数据库验证只使用临时合成数据，插件入口继续使用 R0 兼容层和旧库。

模型能表达目标 Schema 所需的计划时间类型和睡眠阶段，并提供数据/转换约束；这不代表已实现混合规划算法、自主睡眠决策、计时调度或唤醒命令。命令提交和 receipt 幂等属于 R1-03，逐行旧数据导入属于 R1-04，接管和恢复属于 R1-05/06，权限裁剪和事件投递仍待后续任务。

基础模型采用不可变记录，事件 payload/计划修订差异递归冻结；时间在边界归一化到 UTC 毫秒（不足一毫秒向下取整），无偏移时间拒绝直接当作瞬时。Clock 显式处理 IANA 本地日边界，DST 不存在的时刻拒绝，重复时刻要求 fold；不能解释的午夜日边界也拒绝猜测。可控 Clock 的墙钟回拨不改变单调经过时间。

Schema 1 已创建本规范所有 R1 表，R2/R3/R4 表尚未创建。副活动只预留模型形状，R1 聚合与数据库拒绝副活动执行。事件正文/修订/已关闭实际片段不可改写或删除，outbox 的投递状态字段与事件正文分开；尚无投递 worker。计划条目和日期级约定不会自动物化实际活动、真实睡眠或完成证据。`SleepSession.provenance` 和显式完成证据为基础实现的追溯字段。

仓储只在显式 Unit of Work 内写入，默认回滚；提交前检查跨表一致性，失败写入即使被调用方捕获也不能部分提交。数据库条件唯一索引、复合外键、时间形状 CHECK、历史重叠/不可变触发器提供底层保护。完整命令级版本/权限/幂等与 outbox 原子提交协议仍不能据此宣称完成。

### 2.2 R1–R4 隔离实现与宿主边界

`life_state/engine.py` 提供统一命令、版本检查、receipt 和状态/事实/修订/outbox 原子提交；`coordinator/decisions.py` 负责持久依据、预算和迟到结果拒绝；`planning/hybrid.py` 提供三层契约、增量规划与约定保护。模型调用不持有写事务。

`DevelopmentRuntime.run()` 必须显式启动，独立处理 deadline 和评估触发；45 分钟估计检查不轮询模型。睡眠到期发布 `life.wakeup`，逻辑发生与记录时间分开，迟恢复不延长社交有效期；清醒停机区间显示未知，不作为连续活动、习惯或睡眠债覆盖证据。

当前 Schema 4 共 28 表、4 个显式索引、21 个触发器：001 原 checksum 不变，002 增加决策/集成/订阅，003 显式重建副活动支持并增加连续性表，004 增加检查点、决策触发和未知区间。表重建在独占迁移事务前暂时关闭外键强制，事务内复制并完整校验外键/完整性，结束后恢复强制；普通打开不迁移。循环外键的 1→4 升级已测试，只读投影使用读快照。

estimates-v2 仅按主片段推进独立有界估计，债按完整可观察本地日重算。副活动最多两个且必须被兼容主活动覆盖；习惯最多 32 项，每 7 天最多复核一次，至少 3 个日期才激活，不改显式偏好。摘要支持实际片段→摘要→长期汇总，校验来源/范围/checksum；保守清理只移除已覆盖的模型候选正文。实际事实、会话、约定、迁移原行、命令/决策与事件审计保留，原始事实物理压缩归档尚未实现。

`integrations/life_api.py` 与 `compatibility/life_tools.py` 是逻辑门面，**尚未注册为宿主组件**。身份、策略、社交、记忆是内部注入端口，不代表现有外部 API。`plugin.py`、manifest、R0 配置和默认调度未改接；实际模型路由、可信身份入口、Hook/频率移交和接管须经过真实 Runner 门禁。详见 [当前报告](R2_R4_REPORT.md)。

## 3. 权威模型

### 3.1 通用约定

- ID 使用不依赖聊天流的稳定字符串；示例 ID 仅用于文档。
- 数据库时间使用 UTC epoch 毫秒整数，字段后缀 `_at_ms`；API 使用带偏移的 ISO 8601，字段后缀 `_at`。序列化集中实现。
- 每个时间区间使用半开区间 `[start, end)`；相邻活动边界不能同时判定为进行中。
- `timezone` 为 IANA 名称，默认 `Asia/Shanghai`；本地日期独立存 `YYYY-MM-DD`，不得只保存 `HH:MM` 作为执行依据。
- `state_version` 是角色生活聚合版本；`plan_version` 是计划修订版本；`config_revision` 是影响决策的配置版本。影响生活决策的目标、约定、偏好和习惯变更也递增 `state_version`。
- 原始事实不可原地改写；必要的管理纠错通过追加纠错事件及引用原记录的修订表达，重新计算投影。实现不要求完全事件溯源，快照和明细表仍是读写基础。

### 3.2 LifeState

| 字段 | 语义 |
|---|---|
| `character_id`, `state_version` | 聚合身份与并发版本 |
| `sleep_phase` | `awake / winding_down / sleeping / temporarily_awake / waking` |
| `primary_activity_id` | 当前实际主活动；初始化未知时可空 |
| `secondary_activity_ids` | R4 使用，最多两个 |
| `active_sleep_session_id` | `sleeping` 或 `temporarily_awake` 必须非空，其他阶段为空 |
| `last_wake_at` | 最近一次结束完整睡眠会话的时间；未知可空 |
| `awake_since_at` | 当前连续清醒片段开始；睡眠中为空 |
| `energy`, `fatigue`, `stress` | 0–100 的有来源估计；初始化未知可为 null |
| `sleep_debt_minutes`, `sleep_history_coverage_days` | 派生估计和数据覆盖度；无可用历史时 debt 可为 null |
| `active_plan_id`, `plan_version` | 生效计划及版本 |
| `updated_at`, `last_advanced_at` | 最近事务和时间推进水位 |
| `provenance`, `confidence`, `degraded_reason` | 事实来源、初始化不确定性和降级诊断 |

清醒时长从 `awake_since_at` 计算，睡眠总时长从 sleep segments 计算，不同时维护可独立漂移的计数器。查询不触发 LLM；查询展示的派生时长可以随 Clock 变化，但读取不能隐式修改历史。

### 3.3 Activity 与计划

`Activity`：`activity_id`、`character_id`、`role(primary/secondary)`、`category`、自由 `name/description`、`started_at`、`ended_at`、`status(active/completed/interrupted/abandoned)`、`source_schedule_item_id`、`resumes_activity_id`、`sleep_session_id`、`interruption_reason`、`visibility`、`provenance`。完成原因应区分“计时结束”“明确完成”“被替换”。

`ScheduleItem`：`item_id`、`plan_id`、`kind`、`category`、`name`、`time_mode`、`start_at/end_at` 或 `window_start_at/window_end_at`、`preferred_start_at`、`preferred_duration_minutes`、`priority`、`commitment_id`、`status(planned/started/done/cancelled/superseded)`、`source`、`visibility`。

`fixed` 必须有开始和结束；`window` 必须有合法窗口及可容纳的偏好时长；`unscheduled` 不携带假精确时间。`preferred_start_at` 必须在窗口内。进入实际活动由命令创建 Activity，ScheduleItem 只是关联。`done` 只能在实际结果明确后设置，不能仅因旧计划过期设置。

`Plan`：`plan_id`、`character_id`、`level(direction/daily)`、`local_date` 或时间范围、`version`、`status`、`source`。Immediate Intent 保存到决策记录，不另造与 LifeState 竞争的“当前状态表”。

`ScheduleRevision`：基准与结果版本、增删改差异、原因摘要、来源决策、触发事件 ID、时间、操作者。任何约定修改另受约定权限与确认规则控制。

### 3.4 SleepSession 与片段

`SleepSession` 必备字段：

```text
session_id, character_id, kind(night_sleep/nap), status(active/interrupted/closed)
started_at, initial_expected_duration_minutes, expected_wake_at, hard_deadline_at
ended_at, end_reason, decision_id, extension_count, interrupt_until
actual_sleep_minutes, recovery_marker, revision, reason_summary
```

`SleepSegment`：`segment_id`、`session_id`、`started_at`、`ended_at`、`kind(sleep/awake_interrupt)`、`provenance`。片段不重叠，未闭合片段最多一个。`actual_sleep_minutes` 为闭合 sleep 片段之和的缓存，必须可重算。

`sleeping` 对应一个活动中的睡眠主活动片段。中断会结束该片段，建立清醒主活动/休息片段，但保持同一 SleepSession 为 `interrupted`。恢复睡眠创建新的睡眠活动片段并关联同一会话。完整起床关闭会话，清除 `active_sleep_session_id`，进入 `waking`，随后确定性转为 `awake`；不依赖 LLM 解除静默。

### 3.5 约定、目标、偏好与习惯

| 模型 | 必备信息 |
|---|---|
| Commitment | ID、日期/时间窗口、参与范围、来源聊天流、确认依据、`proposed/confirmed/fulfilled/cancelled/missed/needs_review` 状态、版本、可见性 |
| LifeGoal | 目标、阶段、可选 deadline、进度证据、`active/paused/completed/cancelled`、来源 |
| Preference | 内容、`reference/hard_constraint`、来源、是否允许演化、版本 |
| Habit | 归纳内容、证据记录 ID、置信度、权重、最近出现/审查时间、`candidate/active/archived` |
| LifeSummary | 覆盖区间、源记录引用/摘要校验值、摘要文本、重要事实、可见范围、生成版本 |

时间未精确到小时的约定必须保留日期范围及不确定性，不能猜测为零点。旧约定迁移中的 `needs_review` 是保护状态，不得被模型直接清理。

## 4. SQLite 逻辑结构

首个新数据库命名 `maivita.db`，位于宿主确认的持久数据根目录。实现表名按下表固定；索引和 SQL 类型由迁移脚本落实。

| 表 | 主键/关联及用途 | 首次阶段 |
|---|---|---|
| `schema_migrations` | version、checksum、applied_at；新库版本权威记录 | R1 |
| `life_state` | PK character_id；状态投影 | R1 |
| `activities` | PK activity_id，关联角色及可选计划项/睡眠会话 | R1 |
| `plans`, `schedule_items`, `schedule_revisions` | 计划、条目及不可变修订 | R1 |
| `sleep_sessions`, `sleep_segments` | 会话和实际睡/醒片段 | R1 |
| `commitments`, `life_goals`, `life_preferences` | 约定、目标、参考与约束 | R1 |
| `habits`, `life_summaries` | 习惯演化与摘要；R1 预留领域模型，R4 建表 | R4 |
| `life_events` | 事实事件及 outbox 状态；同事务生成 | R1 |
| `event_deliveries` | UNIQUE(event_id, consumer_id)，重试、确认、过期 | R1 |
| `decision_runs` | request_id、输入版本、模型任务、状态、成本、原因、错误 | R2 |
| `command_receipts` | UNIQUE(character_id, idempotency_key)，payload hash、结果 | R1 |
| `migration_runs`, `legacy_import_records`, `legacy_id_map` | 迁移状态、原始行、分类和新旧 ID 映射 | R1 |
| `integration_settings` | 本插件集成模式、配置版本及外部契约声明，不存对方凭据 | R3 |

R0 只实现旧库备份和路径迁移入口，不要求提前创建未使用的新库。Schema 从 1 开始，后续变化逐号递增，不将 R0–R4 等同于数据库版本。新版发现更高且不支持的 Schema 必须拒绝写入。

必须设置外键、busy timeout；允许 WAL，但备份必须覆盖 WAL 中已提交内容。约束至少包括：

- `life_state` 每角色一行；活动中 `role=primary` 且 `ended_at IS NULL` 的条件唯一索引。
- `sleep_sessions` 的 `status IN ('active','interrupted')` 每角色唯一。
- 所有结束时间不早于开始时间；非零时长活动结束必须晚于开始。
- `schedule_items` 的时间模式约束，快照引用必须指向同一角色。
- `state_version`、`plan_version` 单调递增；幂等键及迁移源标识唯一。
- `life_events` 的 `(character_id, state_version, event_index)` 唯一；同一事务可以产生多个有序事件。

跨表睡眠阶段一致性由单一领域服务在事务内校验，数据库外键和唯一索引提供第二道保护。写入失败必须连同 receipts/outbox 回滚，成功后才更新缓存。

## 5. 命令与事务协议

### 5.1 内部命令

```text
AdvanceTime(now)
StartActivity / EndActivity / InterruptActivity / ResumeActivity
ApplyPlanPatch / ProposeCommitment / ConfirmCommitment / AmendCommitment
BeginWindingDown / CancelWindingDown / StartSleep
InterruptSleep / ResumeSleep / ExtendSleep / FinishSleep
UpdateSubjectiveEstimate / CorrectRecordedFact
```

内部命令通用封套：`command_id`、`character_id`、`expected_state_version`、`expected_plan_version`（涉及计划时）、`config_revision`、`idempotency_key`、`issued_at`、`expires_at`、`source`、`actor_context`、`payload`。actor_context 来自适配器验证结果，不能信任外部 JSON 中自报角色或权限。

提交步骤：

1. 验证身份/策略决定的时效、对象和动作范围，计算规范化 payload hash。
2. 查 receipt：同键同载荷返回原结果；同键不同载荷返回 `IDEMPOTENCY_CONFLICT`。
3. 获取短时每角色提交锁，开启事务并重读状态和计划版本。
4. 校验全部不变量、时间、约定、前置状态。版本不符返回 `STALE_STATE`，不部分应用。
5. 纯领域转换生成新快照、活动/会话变更和事件；一次递增聚合版本。
6. 写表、outbox 和 receipt 后提交；释放锁并失效旧投影缓存。
7. 异步投递事件。数据库失败不返回成功；网络消费者失败不回滚已发生事实。

LLM、SDK 网络调用和外部策略请求不得在数据库事务中等待。外部策略许可必须短期有效，提交前核验状态版本绑定；失效则重新裁定。

### 5.2 决策提案

Strategic 与 Immediate 共用输入快照但输出不同。下例是 Immediate 的目标契约，不是现有 SDK 方法：

```json
{
  "schema_version": 1,
  "request_id": "decision-example-001",
  "basis": {"state_version": 41, "plan_version": 7, "config_revision": 3},
  "intent": "start_sleep",
  "reason_summary": "今天较疲惫，明早没有较早的约定",
  "payload": {"kind": "night_sleep", "expected_duration_minutes": 480},
  "plan_patch": []
}
```

请求 ID 和 basis 由程序提供，响应仅回显，程序不得以模型回显值覆盖原请求上下文。程序在提交时分配实际 `started_at`，计算 wake deadline。模型不得写 `energy=0` 来绕过入睡校验、伪造权限或提供 SQL/任意工具调用。

允许的即时意图包括 continue、change_activity、rest、wind_down、start_sleep、propose_plan_patch。睡眠中断/延长仅在对应可信触发下开放，不让通用计划响应任意执行唤醒。

### 5.3 协调与预算

每角色最多一个有效模型决策在途；新触发可合并或排队。正在生成的低优先级结果可以作废，让已授权唤醒和到期状态转换先提交；不阻止确定性计时等待模型。程序推进不必抢占模型网络请求，但提交时一定会使旧 basis 失效。

任务键由角色、任务类型、目标日期/触发聚合 ID 构成。普通检查无变化不创建任务；重复消息使用来源事件 ID 去重。重试次数、超时、每日预算与熔断设置需记录到 diagnostics。请求结果不确定的 RPC 超时标 `unknown_outcome`，不立即复制同一昂贵请求。

## 6. 时间、睡眠恢复与配置热更新

Clock 提供 `now_utc()`、`monotonic()`、`to_local()`、`local_day_bounds()`。运行中等待采用单调计时，持久 deadline 采用 UTC；时钟回拨不得产生负时长，大幅跳变触发校验与一次恢复事件。

时区改变只影响后续计划解释和显示，不改既有实际 UTC 事实。未来使用本地参考生成的计划需产生显式修订；既有明确约定的瞬时时刻不能随配置自动移动。夏令时重复/不存在时间必须显式消歧，不能依赖服务器默认时区猜测。

恢复顺序：阻止外部写入 → 完成版本检查 → 获取单实例权 → 读取一致快照 → 校验活动/会话关联 → 按 deadline 推进可确定转换 → 恢复下次任务 → 开放只读与写入入口 → 投递待发事件。

睡眠未到期：恢复剩余定时，不请求 LLM。睡眠已到期：以原截止时间为 `occurred_at` 记录逻辑结束，`recorded_at` 为当前时间；标记停机恢复，并在“醒来至恢复”的时间段保留活动未知。中断已过期：按预定的恢复/结束规则推进一次。所有定时任务带 `(session_id, revision, deadline_kind)`，旧计时器醒来时发现 revision 不符即退出。

保护上限、延长次数、中断到期和重新入睡冷却见需求规范，不另定义第二套数值。自动从 `waking` 到 `awake` 的转换与 awake fallback 主活动可在同一提交周期完成，不能等待生成回复。

热更新首先验证完整新配置，成功才切 `config_revision`。正在睡眠时修改参考作息不变更会话；修改时区不改 UTC deadline；显式将 sleep.mode 改为 disabled 须由授权命令结束会话、记录配置原因并撤销静默建议。

## 7. 对外接口边界

### 7.1 标识与调用约定

目标插件 ID：`davidblackcn.maibot-maivita-plugin`；仓库/目录名：`maibot-maivita-plugin`；API 版本独立为 `1`。R0 验证 SDK manifest 和注册规则后固定绑定，不凭空认定支持跨插件别名。

以下是 MaiVita 逻辑 API 方法名，实际由 `ctx.api.call` 等 SDK 能力映射。读操作只读，不隐式生成计划或调用模型。

| 方法 | 参数/返回重点 | 可用阶段 |
|---|---|---|
| `get_life_state` | 请求角色/请求范围；返回脱敏 StateView、版本、更新时间、stale/degraded 标记 | R1 |
| `get_current_activity` | 旧形状兼容的当前活动投影 | R0 转发，R1 读新状态 |
| `get_plan` | 日期范围、可见范围；返回计划项及版本 | R2 |
| `get_availability` | 请求来源/行为类别；返回生活限制建议及依据版本 | R2，R3 外部策略接管 |
| `request_replan` | 原因、预期版本、幂等键；返回 accepted/request_id | R2 |
| `request_wakeup` | 请求范围、临时/完整唤醒、可信调用上下文、幂等键 | R2 本地适配，R3 外部权限 |
| `propose_commitment` | 时间/日期范围、内容、来源与可见性；返回提议/确认流程 | R2 |
| `get_diagnostics` | 管理员可见迁移/预算/事件积压/恢复状态，脱敏日志 | R1 |

通用新 API 响应为 `{api_version, ok, data, error, state_version, as_of}`；异步请求 `accepted` 只表示排队，不能解释为状态已变化。`error` 含 code、可读 message、retryable、可选 current_version。标准 code：`NOT_READY`、`PERMISSION_DENIED`、`POLICY_UNAVAILABLE`、`INVALID_TIME`、`CONFLICT_COMMITMENT`、`STALE_STATE`、`IDEMPOTENCY_CONFLICT`、`BUDGET_EXCEEDED`、`STORAGE_UNAVAILABLE`。

兼容 `get_current_activity` 保留旧裸 dict，不套新封套；具体字段和旧命名空间桥接见迁移文档。

### 7.2 生活可用性是建议

```json
{
  "sleep_phase": "sleeping",
  "can_reply_recommendation": false,
  "can_proactively_contact_recommendation": false,
  "interruptibility": "requires_policy",
  "reason_code": "sleeping",
  "state_version": 42,
  "valid_until": "2026-10-21T08:10:00+08:00"
}
```

该结果不授权唤醒、不承诺一定能发送，也不包含私聊约定详情。最终策略结果不能因为 LLM 写了 `can_reply=true` 被绕过。

### 7.3 适配器契约

| Port | 输入与输出 | 降级 |
|---|---|---|
| IdentityPort | 可信平台/会话调用上下文 → 已验证主体和声明 | 无法验证则不赋予特权 |
| PolicyPort | 主体、动作、资源、StateView → allow/deny + 范围、到期时间、依据版本 | 特权操作拒绝；计时与只读继续 |
| ModelPort | 任务类型、裁剪上下文、结构 Schema → 候选提案、调用元数据 | 限次失败后保持状态 |
| SocialEventPort | 已提交且脱敏的生活事件 → 消费确认 | 可离线，禁止 MaiVita 自行补发 |
| MemoryPort | 带可见范围的摘要 → 明确接受/失败 | 本地历史继续保存 |
| HostRuntimePort | 数据根目录、Hook、配置、日志和生命周期 | 缺必需能力时报清晰错误 |

这些 Port 是内部抽象，不是宣称外部插件已实现的方法名。R3 必须对照真实 Owner Auth、策略层和社交插件契约接线并做契约测试。

R0/R1 的管理权限和主动行为在旧逻辑适配器内保留。R2 本地策略适配只覆盖 MaiVita 自身：明确管理员列表、默认睡眠静默和指定唤醒动作；不读取其他插件的配置来构造全局策略。R3 `integration.policy_mode=external` 与 `local_compat` 互斥，启用外部模式后撤销本插件旧频率调整和发送 Hook 的裁定职责。

## 8. Life Event 协议

事件是已经提交的事实，外部请求不能冒充 `life.wakeup`。输入事件使用独立 request/trigger 类型。

```json
{
  "event_id": "event-example-001",
  "event_type": "life.wakeup",
  "schema_version": 1,
  "character_id": "default",
  "state_version": 43,
  "event_index": 0,
  "occurred_at": "2026-10-21T08:10:00+08:00",
  "recorded_at": "2026-10-21T08:10:01+08:00",
  "correlation_id": "sleep-example-001",
  "causation_id": "wake-deadline-example-001",
  "visibility": "public_summary",
  "expires_at": "2026-10-21T08:40:00+08:00",
  "payload": {"sleep_session_id": "sleep-example-001", "actual_sleep_minutes": 480, "reason": "scheduled"}
}
```

事件类型基线：`life.activity.started`、`life.activity.ended`、`life.activity.interrupted`、`life.activity.resumed`、`life.plan.revised`、`life.sleep.started`、`life.sleep.interrupted`、`life.sleep.resumed`、`life.sleep.extended`、`life.wakeup`、`life.commitment.changed`、`life.state.recovered`、`life.protection.triggered`、`life.habit.changed`。

`life.wakeup` 仅表示完整会话结束；临时醒来用 `life.sleep.interrupted`。R0/R1 旧行为桥接事件标 `source=legacy_adapter`。未确认的睡眠计划不会发布 sleep.started。

投递承诺为至少一次；消费者按 `event_id` 幂等。角色内按 `(state_version,event_index)` 排序，不能宣称跨进程网络提供绝对 exactly-once。事件落库后 dispatcher 重试，确认写入 event_deliveries；进程崩溃可能重送但不能重复业务副作用。

`expires_at` 表示主动社交机会有效期，不表示删除事实历史。过期事件仍可供审计/状态同步；主动社交消费者不得补发昨日起床消息。消费者缺失或重试耗尽应可诊断，不能阻塞生活提交。没有聊天内容读取权限的订阅者只获取 public_summary，完整 payload 通过授权查询。

## 9. Hook、缓存与发送控制

已核实旧 Hook：`maisaka.planner.before_request`、`maisaka.replyer.before_request`、`send_service.before_send`。前两者继续以适配器形式注入状态，必须遵守实际 SDK 合并规则，保留其他插件注入与未知字段。重复 attempt 不叠加同一生活块，按请求/会话和 state_version 标记自己的注入段。

全局 StateView 缓存以 character_id + state_version 为核心；计划投影带 plan_version。聊天相关文案和注入缓存以 stream_id + 可见范围 + state_version 区分，禁止复用某私聊的全文投影给群聊。提交后立即失效，不能沿用旧 15 分钟分桶掩盖刚发生的入睡/醒来。

过渡发送许可必须绑定精确消息/请求、会话、用途和短有效期，并一次消费。旧版按会话维护的短暂回执许可需要核查并发串用风险；SDK 无法提供足够消息来源时不扩大例外范围，改用可证明来源的管理路径。频率恢复只能撤销自己注册的 adjustment，不能重置整个会话频率。

## 10. 可测性与完成条件

核心状态转换使用注入的 Clock、随机源（若以后启用）和 ModelPort，不依赖真实等待。测试应能回放 72 小时及 30 天模拟时间，注入任意点进程中止、模型延迟、数据库锁和重复事件。

记录决策输入版本、候选类型、校验结果、事务结果、事件 ID 和状态转换原因；模型私密推理不作为审计需求，只保留可说明决策的简短理由。

架构落地的最低证据：数据库唯一约束有效；事务提交和 outbox 一致；跨聊天流读一致且隐私隔离；模型请求期间不持有数据库写锁；sleep deadline 重启恢复；新旧 API 读同一状态；外部策略切换不留下旧 Hook/频率副作用。完整任务和测试编号见开发计划。
