# MaiVita I1 开发版集成报告

2026-10-21 验收策略更新：本文件中的既往结果保留为历史事实。单插件不再要求独立连续运行 24 小时或 7 天；长期观察、真实平台和生态联调统一移交超级 MaiBot 核心插件联合测试。这不表示这些观察已通过。当前收口状态以 I1_INTEGRATION_READY_REPORT.md 和新版自动门禁为准，生产迁移仍未授权。

日期：2026-10-21。范围：独立完整 MaiBot 测试实例、DevelopmentRuntime 正式插件入口和首个开发测试包。

**结论：限定功能范围的首次安装测试门禁通过，可以安装到全新隔离测试实例。R0 及 R1–R4 的正式阶段验收没有因此通过，生产切换仍禁止。** 本轮没有访问、迁移生产数据，没有修改 Host 或其他插件源码，没有部署、提交或推送 Git。

## 1. 版本、实例与宿主契约

按用户要求核对官方最新版本，并固定到以下已测试组合，不能把未来的 latest 自动视为兼容：

| 项目 | 实际版本 / 位置 |
|---|---|
| MaiBot | v1.3.12，commit `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501` |
| 官方来源 | [Host release](https://github.com/Mai-with-u/MaiBot/releases/tag/v1.3.12)、[固定源码](https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501)、[SDK 2.15.0](https://pypi.org/project/maibot-plugin-sdk/2.15.0/) |
| 完整 Host 源码 | `D:\Dev\maimai-plugin\maibot`，公开归档解包，1877 个源文件哈希锁定 |
| 实际 Host 测试环境 | 本项目 `.integration/i1-host-env`；Python 3.13.6、89 个依赖、SDK 2.15.0 |
| 专用实例 | 本项目 `.integration/i1-instance`，合成插件数据，用户单独填写测试模型配置 |
| 开发包 | manifest `0.2.0`，名称含 I1 Development；配置版本 `2`；默认禁用 |

版本发现证据：[upstream-discovery.json](../../evidence/i1/upstream-discovery.json)。官方 Host 的 uv.lock 实际锁定 SDK 2.12.0，因此测试环境明确覆盖到 2.15.0，未改 Host 锁文件。相邻 Host 的原 `.venv` 仍为 2.12.0，不能与本次验收环境混用；再次按原锁执行 uv sync 会恢复旧 SDK。

用户已明确同意仅此隔离测试实例的 EULA/隐私条款。启动使用 Host 自身协议校验机制；遥测 `telemetry.enable=false`，平台接入、MCP、插件 render 关闭，WebUI 仅监听回环。模型凭据只留在忽略目录内，不进入报告或包。

| 契约 | 本轮核实结果 | I1 处理 / Host 后续需求 |
|---|---|---|
| H01 可信管理发送回执 | SDK 正常 send.text 可用；缺少可证明管理用途并绑定发送 attempt 的可信回执契约 | 不提供静默发送例外，睡眠时命令不发送、不唤醒。将来支持例外需要宿主提供可信、不可由插件参数伪造的授权与回执链 |
| H02 频率归属及撤销 | 未核实到满足 owner/token 归属、只撤销本插件影响的完整接口 | 频率功能严格关闭，不调用全局重置。需要 Host 提供归属设置/查询/释放及卸载语义后另行验收 |
| H03 Owner Auth 身份 | 现有已审计接口不能向本插件提供满足要求的可信调用者声明 | Owner Auth 与管理写入关闭；即使传入 owner 字段也拒绝。后续需要可信用户/会话绑定和授权契约，未经授权不改宿主或身份插件 |
| H04 生命周期及注册 | 完整 Host 的原生 Runner、API/Tool/命令/Hook 注册与正常启停路径可用 | 插件实现加载失败清理、单写者及安全停止；不需要修改 Host 源码。失败注入清理由插件回归验证，正常重载/退出另由真实 Host 验证 |

## 2. 任务与实现

| ID | 完成内容 | 边界 |
|---|---|---|
| I1-01 | 官方版本核对、固定源码与 SDK、H01–H04 再确认 | 不把接口缺失写成已支持 |
| I1-02 | 安装完整 Host、固定依赖、独立环境与空实例、用户协议及测试模型配置 | 不读生产配置，不复制旧运行数据 |
| I1-03 | `development/plugin.py` 将 DevelopmentRuntime 接入正式 SDK lifecycle | 加载先检查旧插件/旧库并取得实例锁，再开新库；第二层 Runtime 锁；失败取消任务并释放资源；无旧调度器 fallback |
| I1-04 | 新旧只读 API、公开状态 Tool、命令及 Hook 接线 | 统一投影到新库；公开摘要及明确 stream 白名单；管理写入安全拒绝；睡眠 before_send 使用 abort 错误策略 |
| I1-05 | `development/model_port.py` 通过真实 SDK llm.generate 路由 | 显式 task/model、UTC 与角色本地时间、严格 JSON、候选校验、超时与预算；不读取密钥或导入 Host 内部模型类，不自动重试未知结果 |
| I1-06 | 完整 Host + 真模型/API/原生组件路径、回归、恢复和来源审计 | 外部平台消息实际送达、24 小时/7 天观察未运行 |
| I1-07 | 白名单开发 ZIP、文件指纹门禁、安装/限制/验收/回滚文档 | 默认禁用；不含私有配置、数据库、日志、凭据、旧 .git 或 Host 环境 |

现有领域模型、Schema 4、规划/实际状态/历史事件的区分保持不变，本轮没有新增生活模拟特性。根目录 R0 `plugin.py`、manifest 和旧日程/固定睡眠入口保持原样；开发包只装配新的 development 入口，不实例化旧 GoalManager。配置变化先停止 Runtime，要求显式重载；不在运行中静默改变策略、模型或数据路径。发现旧 goals.db 时拒绝启动，不自动迁移或回退。

API 的 get_life_state/get_plan/get_availability/get_diagnostics 与旧当前活动别名已接入；管理 API 和变更 Tool 返回明确拒绝。命令使用 Host 真实 text 参数和普通 send.text；不声明可信管理身份。replyer Hook 只维护自身标记块，保留他人内容；planner 仅适配已核实的 extra_prompt 形状，其他形状安全跳过。配置与 manifest 均受原生 Host 校验，使用数字配置版本和三段数字 manifest 版本，没有绕过校验。

## 3. 测试与证据

| 检查 | 结果与证据 |
|---|---|
| 全量 unittest | 200 项通过，0 失败/错误/跳过；[i1-regression.json](../../evidence/i1-regression.json) |
| 全量 pytest | 200 项、68 个子场景通过；[pytest.json](../../evidence/i1/pytest.json) |
| I0 环境回归 | 6 项通过，含 12 个原生 IPC 场景；[i0-suite.json](../../evidence/i1/i0-suite.json) |
| 完整 Host + 真模型 | 官方完整 CLI/Worker/Runner 启动、原生 WebUI 认证、公开新旧 API、写入拒绝、禁用/启用重载、正常退出通过；[full-host-smoke.json](../../evidence/i1/full-host-smoke.json) |
| 原生组件执行 | 完整 MainSystem 初始化、调度与关闭路径；真实 Tool RPC、只读命令分发/范围拒绝、Hook 去重/内容保留、第二写者拒绝；[components.json](../../evidence/i1/components.json) |
| 恢复演练 | 新空临时目录恢复、Schema/状态/事件校验通过；源库不变，临时副本移除；[restore.json](../../evidence/i1/restore.json) |
| 来源与凭据审计 | 223 个原有文件核对；仅允许的 5 份原有说明文档改变；Host 1877 个源码文件、旧插件及身份插件未改；无测试密钥泄漏；[final-audit.json](../../evidence/i1/final-audit.json) |
| 安装包验证 | 52 个文件、逐字节匹配白名单、全新进程导入通过、默认禁用、不含运行配置或数据；[package-verification.json](../../evidence/i1/package-verification.json) |
| 综合门禁 | 11 项检查及候选源码指纹一致，通过限定开发首装范围；[first-install-gate.json](../../evidence/i1/first-install-gate.json) |

收尾再次执行 I1 专项 pytest：11 项、12 个子场景通过；仅有当前沙箱不能创建 pytest 缓存的警告，不影响测试结果。

最终完整 Host 场景实际经 `utils` 路由调用 `deepseek-v4.1-flash`，模型阶段耗时 5.329 秒，合法 `start_activity` 候选提交成功；此后卸载/重载保留新库。该最终候选证据记录 1 次真实模型请求。此前调试尝试保留在专用合成库，决策记录为 applied 3、abandoned 1、uncertain 1；未知结果没有自动重试，不能将这些记录数当成供应商完整计费记录。见 [decision-outcomes.json](../../evidence/i1/decision-outcomes.json)。

I1 测试使用完整原始 Host，未以替身替换 Host 模块。组件验收是本地测试操作者经原生组件执行路径发起，证明注册、RPC 与范围限制；没有连接外部平台账号，不能据此声称 QQ 等平台消息已实际送达。单元回归报告自身的 `real_host_runtime=not_run` 只描述该回归进程；真实 Host 证据单独保存。单次 smoke 的初始综合门禁字段不承担发布裁决，以最终 evaluator 合成报告为准；运行时 diagnostics 也不会自行宣布发布门禁通过。

恢复点为合成新库 state_version=18、event_count=18；恢复副本 SHA-256：`f3eeabd643f763a18cb6ba363e6da46ff583a9f59976741af80a49d22aeb8de8`。演练未激活恢复副本，未改变源库。Host 测试结束正常退出，测试进程树受 kill-on-close Job 约束。

## 4. 开发测试包与复现

[maibot-maivita-plugin-0.2.0-i1-development.zip](../../dist/maibot-maivita-plugin-0.2.0-i1-development.zip)，96,870 字节，52 个文件。

SHA-256：`aac326ea9eba6c7e462f3180c96d12c73987471b03da4baff02e7121029aa896`。

包保留许可证和来源说明，附 [安装](i1/INSTALL.md)、[限制](i1/LIMITATIONS.md)、[验收](i1/ACCEPTANCE.md)、[回滚](i1/ROLLBACK.md) 文档。包内文件指纹清单为 FILES.sha256.json；构建器要求真实 Host、真实模型、组件、恢复、回归及来源审计门禁齐备，并与当前白名单文件指纹匹配。

工作区复现入口（须先确认没有同实例 Host，真实模型检查会产生测试模型调用）：

```powershell
.\.venv\Scripts\python.exe tools/run_tests.py --report-prefix i1-regression
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/run_i1_host_smoke.py --agreements-confirmed --exercise-model
.\.venv\Scripts\python.exe tools/run_i1_components.py
.\.venv\Scripts\python.exe tools/rehearse_i1_restore.py
.\.venv\Scripts\python.exe tools/audit_i1.py
.\.venv\Scripts\python.exe tools/evaluate_i1.py
```

协议开关仅适用于操作者确已同意条款的测试实例。已有包不自动覆盖；重新发布应重新核对候选指纹和证据。生产配置、密钥和运行目录均不随包提供。

## 5. 未解决风险与人工验收清单

以下项目不因开发首装通过而解除：

- [ ] 在获授权的独立测试平台账号/stream 中验证只读命令实际送达、SDK 返回值及失败语义；覆盖睡眠时静默拒绝和不同 stream 隔离。
- [ ] 与真实其他 Hook 消费者共同验证顺序、重复注入、未知参数形状和睡眠发送阻断；planner 的未适配 typed item 形状继续跳过。
- [ ] 在另行确认的可信 H01 契约落地后验收管理回执例外；当前不能绕过睡眠发送限制。
- [ ] H02 频率归属释放、H03 身份认证及 Owner/策略/社交契约另行设计和验收；当前保持关闭或拒绝。任何 Host/其他插件源码修改需另获授权。
- [ ] 持续进行至少 24 小时与 7 天实机观察，包括模型预算、未知结果、重载恢复、锁与进程资源；本轮短程验收不替代长期观察。
- [ ] 正式迁移前另行完成 R0-06/08、旧消费者、真实旧库只读评估及 R1–R4 原阶段门禁。本轮不做生产迁移。

完整 Host 的可选组件存在环境限制：测试实例未配置 embedding 模型，其原生向量回填报告无可用模型；内置 plugin-management 的默认配置尝试写入相邻源码目录，被当前托管沙箱阻止。这里没有修补 Host、授予额外写权限或假称全部可选组件健康；已验证的认证 WebUI 插件 API、MaiVita Runtime 和模型路由不依赖这两项通过。

安装到外部 Host 目录已获用户授权；后续环境写入的自动审批多次超时。为完成隔离验收，环境、实例及缓存改放本项目可写的 `.integration`，只读加载外部 Host 源码，未继续依赖该审批或修改宿主。无因此遗留的 I1 工程阻断；外部平台、身份及长期观察仍按上述清单待验收。

## 6. 回滚与下一入口

先停止请求，禁用/卸载 I1 并确认 worker、coordinator 和两个写入锁释放；故障时停止该独立 Host 进程树。保留新库一致快照、指纹和证据，从测试实例移除开发包。旧版本仅在其独立测试实例和匹配的旧库/旧配置上恢复；不得把 maivita.db 改名 goals.db，不得让新旧调度器并行，不自动降级新库。

本轮没有生产回滚动作。原有 223 个文件基线和 5 份文档原件保存在 `evidence/i1-start/`，只能逐项比对恢复，不能覆盖用户后续改动。下一入口是本报告的人工平台/多 Hook 检查及 24 小时观察，并推进可信宿主契约评审；不是开启生产切换，也不是继续新增生活模拟功能。
