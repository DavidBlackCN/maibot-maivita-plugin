# I1 Integration-Ready 收口报告

日期：2026-10-21（Asia/Shanghai）。版本 0.3.0 / 配置 3 / SQLite Schema 4 / 跨插件契约 1.0.0。

**结论：I1 Integration Ready 自动门禁通过。** 当前源码、真实 Host/模型、回切/恢复和公开 Git 导出证据指纹一致；机器裁决见 [integration-ready.json](../integration-ready.json)。仅供隔离安装及超级 MaiBot 联合测试，不进行生产切换。MaiVita 独立功能开发现已冻结。

## 验收策略

按本轮明确授权，单插件不再要求独立连续运行 24 小时或 7 天。长期观察、真实平台与生态联调统一进入超级 MaiBot 核心插件联合测试；这些项目是移交待办，不是已通过。R0 历史全能力验收不因 I1 降级配置而被改成通过。停止新增生活模拟特性，收口完成后冻结独立功能开发。

## 任务与变更

| ID | 本轮结果 |
|---|---|
| IR-01 | plugin.py → entrypoint.MaiVitaPlugin 正式接入原生 SDK lifecycle；life delegate 运行已有 DevelopmentRuntime、SDKModelPort、API/Tool/Command/Hook |
| IR-02 | 配置 3 显式选择 life/legacy；按被选模式注册业务组件，统一公开 API 静态绑定；engine-authority + revision + 目标配置摘要 + 共享锁 + 快照票据控制离线切换 |
| IR-03 | 两种引擎数据目录分离；共享部署锁及 life 内层锁；迁移同锁、歧义阻止激活；失败清理与回切、一致快照及 checksum 恢复 |
| IR-04 | Owner Auth / 统一策略 / 主动社交契约冻结为 1.0.0，JSON Schema 公开；真实传输未启用，H01/H02/H03 不伪造 |
| IR-05 | 公开 tests、integration_tests、固定公开 Host fixtures、无密钥 CI、开发计划及报告；原件、运行证据、缓存、模型配置、数据与包继续忽略 |
| IR-06 | 静态、209 项单元与 68 个 pytest 子场景、原始 Runner、完整 Host、有限模型、原生新旧切换与恢复；最终综合门禁核对源码指纹 |
| IR-07 | 仅门禁实际通过后生成干净开发包，附安装、契约、限制、验收与回滚；冻结独立功能开发，移交联合测试 |

没有新增生活模拟特性，现有计划/实际状态/历史事件区分、领域约束与迁移 001–004 未改。legacy_plugin.py 原字节保留旧日程/固定睡眠实现；正式路由边界额外拒绝其管理写入，避免因旧组件绕过缺失的可信身份。旧固定睡眠只在明确选择 legacy 时生效，不能与 life 并行。

## 固定环境与真实接线

- Host：1.3.12 / `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`；SDK：2.15.0；Python：3.13.6。
- Host 源码继续来自已审计的相邻独立安装，原始 Python/锁文件未修改。完整 Host 环境位于工作区 `.integration/i1-host-env`，最终合成实例为 `.integration/ready-gated-instance`；没有生产实例或真实平台账号。
- 先前同意的 Host EULA/隐私条款仅用于本项目隔离测试，使用 Host 自身 checksum 机制。遥测、平台服务、MCP、render 关闭，WebUI 仅回环并实际验证 Cookie Auth。
- Host 原锁带 SDK 2.12.0，独立环境明确覆盖 2.15.0。统一配置需要 Manifest 声明的 pytz/pillow；首次完整 Host 发现缺少 pytz 后，仅向隔离环境安装依赖，未改 Host 源码或依赖锁。
- SDK 原生先发现元数据再注入实际配置。已修复默认 disabled delegate 被提前缓存的问题，增加相同顺序的回归；提交 authority 前强制检查 delegate 已就绪且实际持锁。
- 原生 WebUI 在 legacy 模式同样验证静态 API。已补稳定绑定，legacy 返回 ENGINE_UNAVAILABLE，绝不把旧计划伪装成新生活事实。

完整 Host 的只读 API、新旧当前活动、Tool、命令分发/范围拒绝、Hook 内容保留与去重、单写者、正常卸载/重载均经真实 Runner 验证。模型只通过 SDK 正式 llm.generate，不导入 Host 内部模型类、不由插件读取密钥。

## 真实模型与测试结果

最终完整 Host 场景经 utils 路由调用 deepseek-v4.1-flash，合法 start_activity 候选提交成功，模型阶段 5.935 秒，之后禁用/启用及重启保留状态。最终空实例强制 daily_budget=1，持久记录仅 1 条 applied；之后原生组件和回切检查未新增模型 attempt。早期缺依赖/默认 delegate 失败时尚未调用真实模型。

调试旧实例保留 7 条决策记录：4 applied、3 uncertain。补查发现关闭取消时，原触发器可能在重启后重新入队；已将 Integration Ready 暂时撤回并修复：模型 attempt 与批次认领同事务提交，在外部 RPC 前认领；重启兼容收拢旧版留下的取消间隙，冷却/预算延期未发起请求时继续保留触发器。新增取消→关闭→重启回归确认不再调用原触发器。旧审计没有清空或改成成功。本轮两个实例共 8 条决策 attempt（5 applied、3 uncertain），不等于提供方实际 HTTP 次数或计费记录；未知结果不能证明上游停止。

| 检查 | 实际范围 |
|---|---|
| 静态与契约 | Python AST/编译、清单与配置、冻结 JSON Schema 与代码一致 |
| 单元回归 | 209 项通过；包含旧行为、真实临时 SQLite、计划/事实、权限拒绝、模型超时/陈旧结果、取消后重启不重试、迁移原行与事务/恢复故障 |
| pytest | 209 项、68 个子场景通过 |
| 原始 Runner | 6 项环境检查、12 项原生 IPC 场景通过；业务替身边界保留明确声明 |
| 完整 Host | 原始 CLI/Worker/Runner，真实模型/API/Auth、启停和重载；另以原始 MainSystem 验证组件分发 |
| 新旧引擎 | 完整 Host 的 life→legacy→life；第二写者拒绝、管理写入拒绝、新 Life Tool 在旧模式不注册 |
| 故障恢复 | 无票据/错锁/配置篡改拒绝；加载后 authority 写失败清理资源；快照校验、独立目录恢复与源库保留 |
| CI 与公开源码 | CI 配置已纳入 Git，远端 CI 结果不冒充本地结果；干净 Git 暂存内容导出后，209 项/68 子场景和 6 项原始 Runner 环境检查再次通过，无私有历史依赖 |

最终切换演练 authority revision 为 3。life 快照为 state_version=3、events=3，SHA-256 `b0c621dd538a199dd5be86d45672bfd64d087a0cb6370275a048dee7ddb7def1`；legacy 快照 SHA-256 `9aeedaeabb7fc7bc0307eb26a269829a0aa98451163cff0d622daf67cf1ae797`。两个副本 integrity_check 和恢复 checksum 一致，临时恢复目录移除，不激活恢复副本。

来源审计覆盖 207 个公开文件候选、Host 原始 1877 文件、旧插件及 Owner Auth 的只读指纹；均无意外改动，无真实测试凭据或运行目录进入 Git。可公开 Host fixture 保留 GPL LICENSE；不可变 legacy 入口及来源 fixture 的原字节通过 Git attributes 保留，避免换行转换破坏审计。干净导出发现的依赖路径误拒绝也已修复：I/O guard 只读允许实际解释器安装的 purelib/platlib，临时写入与网络限制不放宽。

本地原始证据在忽略的 evidence/integration-ready：offline-checks.json、full-host-smoke.json、components.json、legacy-components.json、restore.json、source-audit.json、acceptance.json、package.json。单次 smoke 的初始 passed/first_install_gate 字段不是最终裁决；综合 evaluator 只读取其实际 smoke_passed 和已跑检查，并核对全部代码指纹。运行时也不会自行宣称发布门禁通过。

## 宿主与生态限制

| 能力 | 当前状态 | 联合阶段入口 |
|---|---|---|
| H01 可信管理回执 | unsupported，睡眠无发送例外 | Host 绑定插件/用途/attempt/stream/TTL/一次消费，另行评审授权 |
| H02 归属频率撤销 | disabled，两个引擎均不写全局调频 | Host+SDK owner-bound 设置/释放、断连卸载语义 |
| H03 Owner Auth | disabled，拒绝 payload 或提示词身份 | 真实可信调用上下文与版本化身份声明 |
| 统一策略 | frozen v1 / disabled | 身份通过仍需策略允许，版本/范围/到期绑定 |
| 主动社交 | frozen v1 / disabled | 公共摘要、同 event_id 去重确认、到期不发送 |
| 平台送达/多 Hook/记忆 | 未运行真实生态 | 超级 MaiBot 联合测试统一验收 |
| 长期观察 | 未运行，不再为单插件门禁 | 联合环境整体观察及资源/预算/性能指标 |

完整 Host 可选 embedding 未配置，原生回填仍报告无可用模型；不依赖这项成功，也未修改宿主掩盖错误。早期托管沙箱下内置 plugin-management 自动配置写入受限；本轮显式执行环境允许其正常加载，不能把旧限制报告当当前失败。源文件审计与隔离边界另行核对。

## 安装、回滚与冻结

默认配置 disabled，须显式确认隔离实例、共享绝对锁和会话白名单。包中保留 LICENSE 与来源，不包含 config.toml、model_config.toml、数据库、日志、密钥、Host 源码、venv 或 Git 元数据。

[安装](../INSTALL.md)、[契约](../CONTRACTS.md)、[限制](../LIMITATIONS.md)、[门禁](../ACCEPTANCE.md)、[回滚](../ROLLBACK.md)。切换工具先停机持锁、保存两库快照、生成新配置及票据；成功加载后才原子提交 authority。回滚用新反向票据和匹配旧库，保留新记录及约定差异；不得将 maivita.db 改名 goals.db，不自动降级或重放已过期社交事件。

自动门禁实际通过后只允许进入联合测试，冻结 MaiVita 独立功能开发。不接管生产数据，不部署，不修改 MaiBot Host 或其他插件源码；实际平台消息、可信宿主能力与长期结果均不冒称已验证。

开发包为 `dist/maibot-maivita-plugin-0.3.0-integration-ready.zip`；包指纹及逐文件验证结果以 `evidence/integration-ready/package.json` 为准。构建器必须重新检查本报告对应的自动门禁和运行文件指纹，缺项或源码变化时拒绝生成包。
