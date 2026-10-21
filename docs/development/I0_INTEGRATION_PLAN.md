# I0 宿主契约与隔离集成方案

2026-10-21 验收策略更新：本文件中的既往结果保留为历史事实。单插件不再要求独立连续运行 24 小时或 7 天；长期观察、真实平台和生态联调统一移交超级 MaiBot 核心插件联合测试。这不表示这些观察已通过。当前收口状态以 I1_INTEGRATION_READY_REPORT.md 和新版自动门禁为准，生产迁移仍未授权。

日期：2026-10-21。停止新增生活模拟特性；I0 只准备和验证集成。R0 正式未通过，R1–R4 正式门禁不变，生产切换禁止。执行结果见 [I0_REPORT.md](I0_REPORT.md)，机器矩阵见 [evidence/i0/contract-matrix.json](../../evidence/i0/contract-matrix.json)。

## 固定对象和证据范围

- SDK：安装包 `maibot-plugin-sdk==2.15.0`，Python 3.13.6、Windows；依赖锁在 `requirements-i0.txt`，实际环境指纹随结果保存。
- Host：[Mai-with-u/MaiBot 固定提交](https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501)，源码版本 **1.3.12**。没有找到可确认的部署 Host 目录，实际部署版本仍待人工匹配。
- 下载公开源码归档，SHA256 为 `5c60746188d9050d972dee03a83a793a049f68f38be476393019ff824e0d70d7`。只解出清单中的 188 个源文件/许可证/元数据到忽略目录 `.integration/host/<SHA>/`；没有运行库、私有配置、凭据和 `.git`。每次执行前后校验全部源文件，变更、重定向或额外文件会阻止执行。
- 真实 `PluginRunner` 子进程、TCP transport、RPC codec/server/client、AuthorizationManager、APIRegistry、ComponentRegistry 以及发送/模型/频率/API 原始 capability handler 均直接从完整原模块导入执行。无 AST 函数抽取，无 Host 源码补丁。
- **完整 Host supervisor、bot.py、平台驱动、真实 provider 和部署配置未启动。** 测试编排器承担 bootstrap/注册的协调，仍使用原始授权与注册表；配置、模型提供方、发送服务和频率存储为明确的替身。因此“真实 Runner 隔离验证通过”不等于“完整 Host 实机验收通过”。

## 契约矩阵

下列路径均相对于固定 Host 源码；SDK 路径相对于安装包 `maibot_sdk/`。状态是针对当前固定版本的证据分类，不承诺整个版本区间。

| ID / 能力 | 分类 | 已核实契约和边界 | 证据 / 后续 |
|---|---|---|---|
| C01 生命周期与目录 | 已支持 | `runner/runner_main.py` 调用 on_load/on_unload/on_config_update、reload/shutdown；PluginContext 注入 data/runtime 绝对路径。测试 reload 后仅一个新 worker | I0-T01/10/11；on_load 部分失败需插件自清理，不能假定 Host 会调用 on_unload |
| C02 插件能力授权 | 已支持 | Runner 闭包绑定 plugin_id，原始 Host AuthorizationManager/CapabilityService 拒绝未授予能力；raw host method 有 allowlist | I0-T00/04；这是插件身份与能力授权，不能充当终端用户 Owner 身份 |
| C03 API/Tool/命令/Hook 注册 | 已支持 | SDK 声明、真实 Runner 调用与 Host 注册表工作；公开/私有 API 跨插件检查，完整旧 dotted alias 可解析 | I0-T02/03；测试夹具承载逻辑 API，默认 plugin.py 未改接。完整平台命令分发/多插件 Hook 链未验收 |
| C04 模型任务/模型名路由 | 已支持，提供方待验 | `capabilities/core.py:_resolve_llm_capability_route/_cap_llm_generate` 接受明确 task_name/model_name；SDK `capabilities/llm.py` 可传两者；Host request_type 由插件 ID 形成 | I0-T05；task=replyer 和 model_name=replyer 区分，旧 model=replyer 按任务别名解析；矛盾参数拒绝。止于合成 provider，未请求真实模型 |
| C05 MaiVita 正式 SDK 接线 | 需要适配 | 独立新引擎在测试插件中用 SDK/IPC 运转；公开 API 默认摘要，伪造 Owner 参数不能触发写入 | I0-T09/04；后续单独实现生产入口选择、旧写入者停写、受信上下文与 ModelPort；当前不启用 |
| C06 可信管理发送回执 | 需要最小 Host 修改 | SDK 可传 kwargs，但 `capabilities/core.py:_cap_send_text` 丢弃 nonce/source_plugin_id，发送服务只有白名单参数；`maisaka_source_kind` 是调用参数，不能证明管理用途 | I0-T06；H01。现有精确令牌机制无法端到端成立，继续拒绝静默例外 |
| C07 频率归属撤销 | 需要最小 Host 修改及 SDK 扩展 | SDK FrequencyCapability 无 owner-aware remove；`capabilities/data.py:_cap_frequency_set_adjust` 忽略 plugin_id/adjustment_id，只转 chat/value | I0-T07；H02。OwnedFrequency 检测不可用并且不发 RPC，不执行全局复原 |
| C08 Owner Auth 身份声明 | 当前版本暂不可实现 | 相邻 Owner Auth 2.1.0 / HEAD `155df1ae0dbde4a8690efb2a082ed9c9c6ede40d` 导入 Command/HookHandler，无 API 声明。按消息 QQ 构建身份提示词，没有可消费的权限声明接口 | I0-01 静态复核，指纹仍与 R3 矩阵一致；H03 为提案。不能解析提示词或借昵称授权，未启动/修改该插件 |
| C09 完整 Host 与平台/provider | 暂未验收 | 缺少确定的目标部署版本和独立测试配置；本轮只执行固定源码的运行时层 | 人工项 M01–M06；无真实平台发送或付费模型请求 |
| C10 生态事件/策略/记忆 | 暂不可对接 | R3 内部 Ports/替身不等于现有 Host 服务；本轮未发现/验证新增契约 | 保留 R2–R4 报告限制，不发明外部 API；I0 不扩展模拟特性 |

## 可复现测试环境

在本项目根目录的 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-i0.txt
.\.venv\Scripts\python tools/setup_i0_host.py
.\.venv\Scripts\python tools/setup_i0_host.py --offline
.\.venv\Scripts\python tools/run_i0.py --verify-environment
.\.venv\Scripts\python tools/run_tests.py --report-prefix i0-regression
.\.venv\Scripts\python -m pytest tests -q --tb=short -p no:cacheprovider
```

首次准备需要下载固定公开源码和安装依赖；有缓存后 setup --offline 与全部测试不需要外部网络。没有缓存时 offline 明确失败。Runner 不加载项目的 `plugin.py` 或相邻插件，仅发现临时清单 `maivita.i0.probe`、`maivita.i0.consumer`；consumer 不运行调度器，probe 仅运行一个现有 DevelopmentRuntime。

临时实例由 launcher 创建，包含合成配置/Schema 4 库、插件目录和日志，退出删除。父进程使用 Windows kill-on-close Job Object：测试编排进程先停在 stdin 门口，分配到 job 后才允许启动 Runner；正常、超时和异常均关闭整个进程树。环境仅继承 OS 必需键，不继承部署令牌、模型密钥、代理或强制兼容设置。自己的 IPC 使用随机令牌与 127.0.0.1 临时端口；错误令牌实际被拒绝。

Python audit hook 拒绝临时目录外写入、非源码读取和非本次 IPC 的外连；只允许 Windows 标准库 socketpair 内部唤醒连接。**它是受信测试夹具的误操作防护，不是恶意插件的 OS 沙箱**；不接受任意插件/数据/远端地址参数，不应拿来运行不受信代码。强进程清理测试只创建无业务的父子 Python 进程。

测试结果在 `evidence/i0/results.json`、`environment-tests.json/txt`；旧 R0/R1/R2–R4 证据不覆盖。`integration_tests/fixtures/` 是测试代码，禁止安装到生产插件目录。

## 最小 Host 修改需求（仅提案，未实施）

### H01：受信管理回执

目标链：SDK SendCapability → Host `capabilities/core.py` → `services/send_service.py`/发送消息模型 → `send_service.before_send` 与结果回执。不能只增加任意 kwargs 透传。

Host 从已认证 RPC 调用生成不可被调用方覆盖的 source_plugin_id，并将受限管理用途、精确 request/message ID、stream、短有效期、一次性 nonce 引用绑定到受信发送上下文。普通文本和不受信 metadata 不能产生该声明。管理权限应在用途签发处检查；Hook 只消费与本条消息完全匹配的声明，重试/失败/并发时的消耗规则必须明确，卸载清理本插件的未消费声明。文本、图片、合并转发与后台回执要逐项支持或明确拒绝。平台 delivery receipt 与授权发送凭证应分别建模。

验收：两插件不能冒充来源；同流普通回复不能消费管理凭证；跨流/过期/重放/错误消息均拒绝；发送失败/取消后不产生宽泛许可；多个 Hook 保留未知字段；日志不记录原始密钥/令牌。新接口名称和版本需 Host/SDK 维护者确认，当前不存在。

### H02：归属频率 adjustment

目标链：SDK `capabilities/frequency.py` → Host `capabilities/data.py` → `chat/heart_flow/heartflow_manager.py` 及频率读取/计算服务。增加按 Host 绑定 plugin_id、adjustment_id、chat_id 保存和撤销自身 adjustment 的契约，禁止调用方替换 owner。必须先明确多个 adjustment 与基础分时频率的组合规则、范围、TTL、幂等和卸载/Runner 断连回收策略。

保留旧全局接口的兼容语义，不能将旧接口重新命名后声称具备归属。新 SDK 代理/能力声明和 Host 实现需一起发布、版本探测；缺失时 MaiVita 继续拒绝调频。验收两插件叠加/独立删除、重载、崩溃、过期与跨 chat；撤销 A 后 B 与基础频率保持正确，不能通过“写回 1.0”伪装撤销。

### H03：可信用户上下文与 Owner Auth

RPC 当前能证明调用插件，无法凭 API kwargs 证明某终端用户刚刚执行了管理操作。需要 Host 从已接收的消息/命令上下文派生、限制用途和有效期的身份引用，再由有明确契约的身份服务查询/裁定；只传 QQ/owner=true 仍可伪造。Owner Auth 如扩展版本化 identity API，应定义 platform/user/群范围、有效期、claim 来源、调用方权限和最小可见信息。这个改动涉及其他插件，**本轮没有实施，后续需单独确认修改范围**。

若暂不扩展 Owner Auth，只能在可信 Host 命令入口使用明确的本插件管理员策略；新公开 API 的管理写入继续默认拒绝，不能以本地配置或提示词冒充 Owner Auth 验收。

### H04：生命周期边界评估

固定 Runner 的 on_load 失败分支会 unregister/deactivate/purge，未自动调用 on_unload。MaiVita 正式适配器应在自身初始化异常时释放 worker/lease；这是插件适配责任，可先做到，无需修改 Host。若 Host 后续统一失败清理，应定义幂等约束并单独批准/回归，不能无依据直接补 Host 调用。

## 后续集成顺序与人工门禁

1. **M01**：确认目标 Host 路径/固定 SHA、SDK 和 Python；比对本矩阵，使用完全独立的测试实例/测试账号/空目录。确认旧插件和 R0 调度均未载入，只有一个写入者。
2. **M02**：确认 H01/H02 的版本化契约和修改授权，再在 Host 分支实现/审查；回归权限拒绝、跨插件并发、发送失败和归属撤销。未经确认不修改 Host/其他插件源码。
3. **M03**：明确 H03 的身份来源；检验假冒 QQ/Owner 参数、过期身份、群范围、私密字段均默认拒绝。真实 Owner Auth 无 API 时维持缺口。
4. **M04**：在测试 provider/测试模型上验证完整 service resolver、模型别名、强制模型、取消/超时/重试与预算；本轮仅证明 capability 路由，不证明真实提供方可用。避免复用旧 pinned_model_adapter 的 Host 内部 import 作为新接线方案。
5. **M05**：单独完成 R1-05/07、R2-07 的实际 SDK adapter：入口显式选择、停止旧写入者、同库新旧 API 投影、固定睡眠兼容和失败自清理。测试多插件完整 Hook 链、平台命令/Tool 权限和全部回执类型。不要同时启动两个调度器。
6. **M06**：完整测试 Host 上的启停/热重载/异常退出、合成库备份恢复和单写者验收；随后按原门禁进行至少 24 小时、7 天受控观察，记录真实异常。离线回放不计观察时长。
7. **M07**：各阶段正式验收和另行授权齐备后才能讨论生产迁移；当前没有生产切换授权。

I0 可继续的工程不依赖 H01/H02 提前伪装可用。回退时停止 I0 命令，确认 Job 关闭；临时实例自动清理。默认 plugin.py、数据库 Schema、R0 配置和相邻插件未改，无生产回滚动作。需要撤回文档时使用 `evidence/i0-start/` 中的原件逐项恢复，保留本轮证据；不能把 Schema 4 文件重命名为旧库。
