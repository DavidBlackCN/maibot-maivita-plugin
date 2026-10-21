# I0 宿主集成准备与隔离 Runner 验证报告

2026-10-21 验收策略更新：本文件中的既往结果保留为历史事实。单插件不再要求独立连续运行 24 小时或 7 天；长期观察、真实平台和生态联调统一移交超级 MaiBot 核心插件联合测试。这不表示这些观察已通过。当前收口状态以 I1_INTEGRATION_READY_REPORT.md 和新版自动门禁为准，生产迁移仍未授权。

日期：2026-10-21。**I0-01–04 已交付固定版本的集成准备与隔离验证；R0 未正式通过，R1–R4 未正式验收，生产切换仍禁止。** 停止新增生活模拟特性，默认 R0 入口、Schema 4 核心和兼容行为未修改。

## 任务与实现

| ID | 本轮交付 | 状态 |
|---|---|---|
| I0-01 | SDK 2.15.0、公开 Host 1.3.12 固定 SHA、Owner Auth 2.1.0 审计；C01–C10 契约矩阵 | 完成固定版本审计，部署版本未确认 |
| I0-02 | `requirements-i0.txt`、归档 SHA/188 文件锁、`tools/setup_i0_host.py [--offline]` | 完成，可重建、离线核验，保留 GPL 许可/来源 |
| I0-03 | `integration_tests/` 独立临时实例，原始 Runner 子进程和运行时模块，两个仅测试插件 | 完成，未启动完整 Host 或旧调度器 |
| I0-04 | IPC、环境、异常清理测试及全部现有回归；本报告、计划更新和范围审计 | 通过下述自动测试，未代替实机验收 |
| I0-05–07 | 部署版本匹配、Host/身份契约修改评审、正式 adapter 接线、完整 Host 与长期人工验收 | 未完成，本轮未越权实施 |

已读取 R0、R1、R2–R4 报告和设计、架构、迁移、开发计划。工作前归档 192 个既有文件指纹及将修改的文档原件到 `evidence/i0-start/`；工作区及祖先未发现适用 AGENTS，本项目无 Git 仓库。公开 Host 自带 AGENTS 已读取，其源码保持原字节。

测试 Host 为 [Mai-with-u/MaiBot / 5c7637e6913c84d1c3d5e742fa9e95a7e70d3501](https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501)，源码版本 1.3.12；SDK 为实际安装的 2.15.0，Windows / Python 3.13.6。没有找到可确认的目标部署 Host 源码位置，因此本报告不代表实际部署版本验收。

`integration_tests/launcher.py` 只生成临时 probe/consumer 清单和合成配置/数据库；probe 通过注入路径启动一个已有 DevelopmentRuntime，consumer 不运行调度器。`tools/i0_runner_bootstrap.py` 安装测试 I/O guard 后执行**原始 Runner 入口**。真实 TCP transport、RPC client/server、授权、注册表和 capability handler 从固定完整模块导入，无 AST 抽取或 Host 源码改写。

测试编排器替代完整 supervisor；配置、provider、send service 和 frequency backend 明确使用替身。这保留了实际权限检查和 capability 参数处理，但没有覆盖完整 Host 的授权编排、模型配置 resolver、平台分发及生态插件链。测试夹具没有把不存在的宿主接口注册成可用能力。

## 审计结论与 Host 修改需求

详细矩阵与可供评审的修改范围见 [I0_INTEGRATION_PLAN.md](I0_INTEGRATION_PLAN.md)，机器版见 [contract-matrix.json](../../evidence/i0/contract-matrix.json)。

- **已支持**：原始 Runner 生命周期、注入持久/运行目录、API/Tool/Command/Hook 声明与调用、公开/私有 API 检查、完整旧 dotted alias、插件调用身份绑定与 capability 拒绝。明确 task_name/model_name 和旧 model 别名能经 SDK/IPC 到达原始 Host 路由。
- **需要 MaiVita 适配**：正式生产入口、SDK ModelPort、生命周期失败自清理、可信命令上下文与新 API 绑定。当前仅在测试插件接现有核心，默认 plugin.py 继续 R0；未停写/切换真实旧库。
- **H01 最小 Host 修改**：`_cap_send_text` 丢弃回执 nonce/source；需要 Host 绑定来源、管理用途及精确消息/请求/流、TTL、一次性消费，并贯通发送 Hook/结果。任意 kwargs、`maisaka_source_kind` 或聊天提示词不能证明可信管理回执。
- **H02 最小 Host + SDK 修改**：当前频率只有全局 chat/value，无 adjustment 归属撤销；需要 Host 绑定插件 owner，明确组合规则和自身 adjustment 的设置/删除、重载/断连回收。不能写回 1.0 覆盖其他插件。
- **H03 暂不可实现的身份契约**：本地 Owner Auth 2.1.0 只有消息身份提示注入，无导出 API；源码与 R3 指纹完全一致。需要可信 Host 用户上下文与版本化声明/裁定契约，另行确认其他插件修改范围。当前新公开 API 写入默认拒绝，不解析提示词、不相信 owner=true/QQ 参数。
- **H04 生命周期适配风险**：固定 Runner 的 on_load 失败分支未自动调用 on_unload；正式适配器必须自己清理部分初始化的 worker/lease，不依赖假想 Host 行为。

H01–H04 均为文档提案或插件适配要求。**没有修改 Host、Owner Auth 或任何相邻插件源码，也没有绕过授权。** 未核实的外部策略/社交/记忆契约仍保持 R2–R4 报告限制。

## 自动测试结果

| 命令 / 证据 | 结果 |
|---|---|
| `tools/setup_i0_host.py --offline` | 188 个原始文件校验通过，未启动 Host |
| `tools/run_i0.py --verify-environment` / `evidence/i0/results.json` | **6 tests 通过**，其中原始 Runner 场景包含 **12 项 IPC 检查**；另有 4 项非法源码路径子场景 |
| `tools/run_tests.py --report-prefix i0-regression` / `evidence/i0-regression.json/txt` | **189 tests 通过，0 failures/errors/skips** |
| `python -m pytest tests -q --tb=short -p no:cacheprovider` / `evidence/i0/pytest-result.json` | **189 passed，56 subtests passed** |
| `python -m pip check` | No broken requirements found |
| `tools/audit_i0.py` / `evidence/i0/final-audit.json` | 范围/源文件/旧项目和 Owner Auth 指纹校验通过，无临时业务库/日志留在项目 |

I0-T00–T11 分别验证：错误 IPC 令牌拒绝；实际 SDK 与路径；四类组件注册调用；跨插件 API/旧 alias/私有拒绝；绑定插件身份、能力拒绝与假 Owner 拒绝；任务和模型名路由/冲突拒绝；发送回执元数据丢失复现；归属频率不可用时零 RPC；外连和源码写入拒绝；现有生活引擎经 SDK 接合成候选；配置更新和热重载；正常卸载/撤销授权/移除 API。

reload 前后已提交 winding_down 状态保留，worker 活跃且 lease 可重新获得；最终 loads=2、unloads=2、Runner exit=0。未启动旧 GoalManager/R0 插件，无新旧双调度器。环境测试另验证凭据/代理/强制兼容设置不继承、非源码读取拒绝、Windows Job 关闭杀掉无业务子进程、launcher 超时清理临时实例、源码篡改/额外文件/非法路径拒绝。源码拒绝测试仅使用自造临时样本，没有改写 Host fixture。

I0-T06/07 **通过的含义是复现接口缺口并安全拒绝副作用**，不是回执/归属撤销能力通过。原有 189 项回归未包含真实 Host；其报告 `real_host_runtime=not_run` 只描述该独立回归命令。新增真实 Runner 证据单独保存在 I0 报告中，旧报告未覆盖。

初始化调试中真实清单和生命周期校验曾拒绝不完整测试夹具，现已修正夹具；保留原始 validator、权限检查和版本限制。最后新增的源码校验测试也修正了 Windows 临时路径短名称差异。最终结果均为通过。

## 安全边界与未解决风险

每次生成随机 IPC token，使用自己 127.0.0.1 的临时端口，环境按 OS 键白名单生成；只读取固定公开源码和本项目代码/SQL migrations。所有配置、运行库和日志位于自动清理的临时实例。Windows kill-on-close Job 在启动门口放行前分配，异常/超时也清理进程树。Python audit hook 为受信夹具的误操作防护，不能代替恶意代码的系统沙箱；不能拿测试目录加载任意插件。

真实模型请求 **0**、平台消息 **0**、生产数据访问 **0**；部署配置和凭据未使用。完整 supervisor/platform/provider 和多插件完整 Hook 链未运行；真实账号权限、提供方取消/重试、平台回执质量未验收。源文件固定版本的结果不能泛化到整个 manifest 版本范围。

R0-06 仍被 H01/H02 阻断。R0-08 已获得独立真实 Runner 的运行时证据，但尚缺完整 Host 和实际业务人工验收；R1-05/07、R2-07 正式接线未发生，Owner Auth 和外部生态未联调，至少 24 小时/7 天观察未运行。不能把本轮约 3 秒的 IPC 测试视为长期实机观察。

## 人工验收清单

以下全部待人工确认，具体场景见方案 M01–M07。

- [ ] 确认实际目标 Host SHA/SDK/Python，与固定版本差异逐项审计；复现 offline setup、I0 和回归结果。
- [ ] 核对独立测试实例、合成库和测试账号；只装测试所需插件，确认旧、新调度器不会同时运行。
- [ ] 审查并明确授权 H01/H02 修改范围，再实现可信回执和归属调频；验证跨插件冒用、并发、超时、失败、卸载和崩溃回收。
- [ ] 确认 H03 可信用户上下文及身份接口；普通文本/昵称/伪造 QQ、Owner 参数不可产生管理权限或私密输出。
- [ ] 在独立完整 Host 上验证真实模型 service resolver、指定任务/模型、超时取消/重试预算；本轮合成提供方不计真实模型验收。
- [ ] 单写入者下验证正式 API/Tool/命令接线、旧完整路由、多 Hook、固定睡眠/管理回执、异常初始化/卸载与合成库备份恢复。
- [ ] 通过相应门禁后开展至少 24 小时和 7 天受控观察；任何生产切换仍须另行授权。

## 回退与下一入口

本轮无生产切换，无生产数据回滚。停止 I0 命令后 Job 自动关闭、临时实例删除；固定公开缓存留在忽略目录供复现，不影响默认入口。原始报告、设计/迁移文档、许可证、plugin.py、配置/manifest、领域/存储代码及 migrations 均与 I0 开始前一致。修改的既有文件仅为 `.gitignore`、开发计划、架构、README、CHANGELOG、SOURCES；需要撤回文档时逐项使用 `evidence/i0-start/` 原件，保留测试证据和用户改动，不删除整个工作区。

下一入口 **I0-05**：确定部署版本、评审 H01/H02 和身份可信链；之后再进行 I0-06 正式 SDK adapter 与完整 Host 隔离验收。开发计划已升至 v0.12，冻结模拟特性。未部署、未提交/推送 Git，旧项目及 Owner Auth Git 工作区均保持干净。
