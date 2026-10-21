# MaiVita R0 交付报告

2026-10-21 验收策略更新：本文件中的既往结果保留为历史事实。单插件不再要求独立连续运行 24 小时或 7 天；长期观察、真实平台和生态联调统一移交超级 MaiBot 核心插件联合测试。这不表示这些观察已通过。当前收口状态以 I1_INTEGRATION_READY_REPORT.md 和新版自动门禁为准，生产迁移仍未授权。

日期：2026-10-21（Asia/Shanghai）。交付版本 0.1.0，配置版本 1，阶段 R0；旧 schema 仍为 goals + metadata=1。**本地实现和回归已交付，R0 完整退出门禁未通过，不应据此激活生产或跳过 R0-06 进入正式 R1 切换。**

## 任务完成情况

| ID | 状态 | 本轮产物与证据 |
|---|---|---|
| R0-01 | 完成本地核对 | 无适用 AGENTS.md；新目录原有五份文档，无 Git；旧工作区干净，5.1.0 / `6c936467...`。源码/manifest/数据库/公开参数指纹在 `evidence/baseline.json` 与 `tests/fixtures/legacy_entrypoints.json` |
| R0-02 | 完成 | 70 个白名单公开源码/测试/资源文件建立基线；五份原文档和 LICENSE 哈希保持。未复制数据、凭据或 .git；来源见 SOURCES.md |
| R0-03 | 完成 | fork 固定 `01ce890f...`，逐项选择记录见 PATCH_SELECTION_R0.md；未全量合并或降级本地有效修复 |
| R0-04 | 完成本地实现 | 新插件 ID、展示名 MaiVita、0.1.0、配置 1、角色 default；强类型新配置投影旧引擎；转换报告及示例；新安装管理员默认拒绝，显式转换保留旧空列表语义 |
| R0-05 | 完成 R0 范围 | SDK/绝对路径、旧路径发现、只读结构预检、WAL backup、dry-run 和 checksum restore；未实现/宣称新 schema 导入或激活 |
| R0-06 | **部分完成，实机缺口** | Hook 双载荷、完整参数、追加/去重、卸载与热重载已实现并测试；精确令牌和归属调频适配已测试。已核验 Host 不透传可信回执令牌、无按插件撤销调频接口，因此安全拒绝，睡眠管理回执和旧调频无法保持完整可观察行为 |
| R0-07 | 完成离线契约，未实机联调 | 真实 SDK → 固定 Host APIRegistry/解析器/调用代码 → 新插件处理函数，从旧完整路由查询成功；同一引擎、旧返回结构；实际 Runner 与消费者仍待验收 |
| R0-08 | 完成本地回归/恢复，**实机门禁未完成** | 原回归与 T01–T05 本地矩阵通过，合成恢复演练通过；宿主人工场景未运行，两项接口缺口未关闭 |

## 变更与行为边界

- 旧日程生成、指定模型绑定、最终校验、跨午夜、生成协调和固定窗口判定保留。原有公开文案断言没有删除。实际基线还有 `update_schedule_v4`，连同另外四个 Tool 一并保留。
- `planning.mode=legacy`，`sleep.mode=legacy_fixed/disabled`；拒绝 autonomous 配置。`routine.preferred_*` 不控制 R0 固定窗口，窗口来自 `compatibility.legacy_sleep_*`。
- 数据目录必须明确；GoalManager 先绑定路径，业务服务不再各自回退源码目录；日志和图片跟随同一根。初始化前检查版本/结构，损坏或未知库不改写、不删库重建。启动自动备份也是 SQLite backup，失败停止加载。
- `storage.writer_lock` 必须是本部署共享的绝对路径；OS 锁拒绝第二个 MaiVita 进程，崩溃退出自动释放。加载时查询宿主注册表拒绝完整旧插件。**未改造的旧插件不会遵守新锁，宿主仍须保证旧插件不在新实例启动后被再次启用**；多个实例配置不同锁路径也不在保证内。
- 发送许可绑定随机令牌、精确会话、来源插件、一次消费和 10 秒截止，不再按会话 depth 放行。已审查 Host 丢弃该令牌，故睡眠中连管理回执也会被阻止；不会回退到不安全的会话例外。
- SDK 2.15 的 frequency.set_adjust 只设全局值；Host 也没有 adjustment_id 撤销。当前适配器拒绝调用，保留其他插件频率值。计算逻辑继续回归；拥有归属接口的 Port 替身已验证只撤销本插件条目。**这不是已实现的宿主新接口**。
- 旧 API 全路由通过 Host 对 `entry.name` 的点号精确匹配 alias 实现，并与新方法共享服务。没有运行第二个旧引擎，也没有另写数据库。

## 验证

环境：Windows 11、Python 3.13.6、真实 `maibot-plugin-sdk==2.15.0`。依赖在本目录 `.venv`，可复现入口 `tools/run_tests.py`；完整版本见 `evidence/environment.json`。

| 验证 | 结果/范围 |
|---|---|
| 原 65 项回归 | 通过，覆盖 model_selection、generation_coordination、response_parser、time_constraints、apply_and_frequency、sleep_public_outputs、plugin_runtime、manifest、config_example、frequency_diagnostics |
| T01 | 路径不存在/候选歧义/目标已有文件、未知版本与额外列、坏库、配置转换及未知字段拒绝、原文档指纹通过 |
| T02 | 活跃 WAL 已提交记录进入一致快照；只读预检不改业务数据；备份失败拒绝继续初始化；SHA256 恢复与 CLI dry-run/restore 通过 |
| T03 | 两种 planner 载荷、其他插件字段、上下文追加、重试/重复块去重、SDK 失败；真实 Host 合并函数参与验证 |
| T04 | 真实 SDK 代理和固定 Host 注册/解析/调用代码下，旧完整路由及新路由同时可调用、形状一致；基础设施用替身，无真实 IPC/模型/发送 |
| T05 | 新旧权限语义、普通同会话并发发送不串用令牌、来源/过期/一次性、令牌异常回收；归属 Port 清理与当前 SDK 安全拒绝；跨进程锁、加载冲突、失败加载回收、卸载重载与启停通过 |
| 全套最终结果 | **96 tests，0 failures/errors/skips**，见 `evidence/test-results.json`、`test-results.txt`；pytest 复核 **96 passed、31 subtests passed**，无警告 |
| 恢复演练 | `tools/rehearse_restore.py`，结果 `evidence/restore-drill.json`：仅合成 active/completed 两条未来约定，WAL 保持打开，副本恢复 2/2、状态与参数不丢、源 DB/WAL 哈希不变、恢复 SHA256 一致、integrity_check=ok；临时数据库自动清理 |

本地测试没有跳过 SDK 测试；这不等于真实宿主联调。测试会执行固定 Host 文件中的原始类逻辑，省去宿主导入与启动，只对日志/超时/监督器输送提供替身。ContextItem 时间/ID/形状已检查，但真实反序列化器、Runner IPC、权限授予、平台发送及真实消费者未联调。

额外存储检查证明：`mode=ro` 打开已关闭 WAL 库也可能创建 sidecar。工具因此在缺 sidecar 时默认拒绝，显式 `--offline` 仅接受已停止/checkpoint 且无 WAL 的源；测试验证源文件清单和哈希不变。运行时本插件已持有写入者锁的自有目录允许 SQLite 正常创建 sidecar，不把这一权限扩展到迁移源。

## 未完成与已核实限制

1. **R0-06**：需宿主支持可信来源的精确管理回执路径，以及可按插件/adjustment_id 删除的频率接口。现有 SDK/固定 Host 证据证明不能安全实现；本轮不越界修改宿主或旧仓库。不应把拒绝副作用当作“旧行为完全保留”。
2. **R0-07/08 实机验收**：无授权的测试 Runner/部署配置；未运行真实生成/查询/引用命令/平台回执、多插件真实 Hook、旧全路由消费者及频率卸载场景。候选最低 Host 1.2.3 和 SDK 2.15.0 不代表版本区间全部兼容；精确证据只覆盖记录的 SHA/SDK。
3. 指定模型仍保留旧 `pinned_model_adapter` 对宿主 `TaskConfig/LLMOrchestrator` 与模型配置路径的依赖；替身回归通过，未读取真实模型配置、凭据或发起模型请求。运行环境是否允许这些内部依赖仍待验证。
4. Owner Auth、统一策略、社交消费者和 MemoryPort 未接入，属于 R3；未假造外部契约。R1 schema/逐行分类导入/outbox、R2 自主睡眠与混合规划、R4 连续性均未开发。
5. R0 仍为旧 schema/旧调度，旧清理与缓存模型保留；没有假称已满足 R1 的事实状态、原子 outbox、逐行约定保护或实际睡眠历史保证。无历史事实补造。

## 回滚

本轮只改新目录并安装隔离依赖；旧目录最终逐文件校验和 Git 状态应与基线一致，结果见 `evidence/final-audit.json`。没有生产切换，不存在生产数据回退窗口，也未创建 Git 提交或推送。

将来受控启用 R0 后回退时：停止新命令/生成/调度和插件 → 确认任务结束并释放锁/本插件归属调整 → 保存当前库一致快照及 T0 后目标/约定/取消差异 → 在**独立空恢复路径**用 `r0_migrate.py restore --sha256` 恢复已验证 T0 旧 schema 快照 → 核验旧代码、旧配置、完整性和旧路由 → 只启动一个旧实例。原库/备份保留；不得把未来 R1 `maivita.db` 改名成 `goals.db`。新运行后产生的差异须对账，已发送消息不能回滚。

## 后续授权与当前状态

进一步授权已扩展至剩余 R1 及 R2–R4，开发计划为 v0.11；本文件保留 R0 初次交付的历史含义。最新实现与验收边界见 [R2–R4 报告](R2_R4_REPORT.md)。R0-06/08 和正式门禁仍未通过，默认 R0 入口、固定睡眠及旧 API 未改接，未生产切换。

## R1 入口（初次交付记录）

后续授权更新（2026-10-21）：[开发计划 v0.10](DEVELOPMENT_PLAN_R0-R4.md) 已允许 R1-01/02 基础工程与 R0 实机验收并行；本报告中 R0-06、R0-08 和正式退出门禁仍未通过。该授权不允许生产切换、新引擎接管或提前实现 R2。R1 基础实现、回归和后续入口见 [R1 基础交付报告](R1_FOUNDATION_REPORT.md)。R1-04 才实现逐源行分类导入。
