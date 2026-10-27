> 2026-10-26 兼容性修订：运行时仅接受 `Host 1.3.12 / SDK 2.15.0` 与 `Host 1.3.15 / SDK 2.16.0` 两个明确配对；Manifest 的上下限不表示支持中间版本或混搭。部署时使用 Bot 自己的虚拟环境，不要用系统 Python，也不要单独降级 SDK。新版验收证据与旧版分开记录；真实 QQ、生产部署以及 H01/H02/H03 均未因此获得验收或授权。

# maibot-maivita-plugin

MaiVita 是 MaiBot 的生活状态引擎，维护角色共享的当前活动、计划、睡眠和历史事件，为 planner、replyer 与其他插件提供一致的公开生活上下文。

- 当前版本：`0.3.1`
- 新目标：Host `1.3.15`（`062d45cf8bd4272ac57d7ec03fcf405e5a458211`）/ SDK `2.16.0`；I1 工具使用 `MAIVITA_HOST_BASELINE=1.3.15`。
- 保留原基线：Host `1.3.12`（`5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`）/ SDK `2.15.0`；I0 固定夹具继续验证此组合。
- 配置版本：`3`；SQLite Schema：`4`；跨插件契约：`1.0.0`
- 项目地址：[DavidBlackCN/maibot-maivita-plugin](https://github.com/DavidBlackCN/maibot-maivita-plugin)

本版用于独立实例与超级 MaiBot 核心插件联合测试。Integration Ready 的范围见[验收说明](docs/ACCEPTANCE.md)及[验收状态](docs/integration-ready.json)，不代表生产切换许可。长期观察和真实生态联调统一在联合测试阶段进行，独立功能开发保持冻结。

## 功能

### 单一生活状态

- 各聊天流读取同一持久生活状态，公开摘要不暴露私密约定或来源。
- 严格区分计划、实际活动、睡眠会话与历史事件；模型不能补造实际历史、授权或履约证据。
- 以状态版本、幂等命令与 SQLite 事务保护状态/历史/outbox 原子一致。
- 重启按已提交 deadline 恢复；无法观察的停机活动保持未知。

### 规划与睡眠

- 正式 lifecycle 接入现有 R1–R4 Life Runtime、混合规划、睡眠、连续性与查询模块。
- 模型通过 SDK `llm.generate` 显式任务/模型路由，严格 JSON、候选校验、预算、超时和陈旧结果拒绝；不自动重试未知结果。
- 查询不调用模型；睡眠计时独立恢复，模型不定时轮询睡眠状态。
- 旧固定睡眠引擎作为 `legacy` 配置选项保留，必须经过受控离线切换。

### 宿主接线与安全降级

- API、只读 Tool、命令和 Hook 使用真实 SDK/Runner 注册。
- 新旧完整当前活动 API 路由投影到唯一被选引擎。
- 无可信管理回执时，睡眠发送不设例外；无归属撤销接口时，调频关闭。
- Owner Auth、统一策略和主动社交尚无已核实传输，接口形状冻结，功能关闭；管理写入安全拒绝，不从提示词或参数推导权限。

## 安装

1. 使用上述支持的 Host/SDK 配对。Host 1.3.15 使用 SDK 2.16.0，不要单独降级 SDK。
2. 将仓库或已通过门禁的包放入插件目录，同一实例只保留一个 MaiVita ID；停止旧完整规划插件。
3. 首次安装由 Host 生成默认配置，默认启用 life；也可复制 [config.example.toml](config.example.toml)。不需要确认口令、锁路径或会话 ID。Snowluma 已限制测试群时，直接发送 `/maivita status`。

```toml
[plugin]
enabled = true
config_version = "3"

[engine]
mode = "life"

[instance]
writer_lock = ""
allowed_streams = []
timezone = "Asia/Shanghai"
frequency_enabled = false
owner_auth_enabled = false
privileged_writes_enabled = false
```

模型提供方和密钥只在 Host 专用 `model_config.toml` 中管理。详见[安装与引擎切换](docs/INSTALL.md)。配置 1/2 不自动升级，不能只改版本号或复制旧运行数据库。

## 命令与 Tool

life 模式支持 `/maivita`、`/plan`、`/规划` 的以下只读查询，默认作用于 Host/适配器接入的会话；非空 allowed_streams 可进一步限制。命令走正常发送权限，睡眠时不会唤醒或绕过静默。

| 子命令 | 说明 |
|---|---|
| `status` | 当前公开活动和睡眠状态 |
| `plan` | 计划和公开安排 |
| `availability` | 忙闲与下次到期时间 |
| `diagnostics` | 版本、降级、最近模型状态 |

`maivita_status` Tool 提供同样的公开查询；旧状态 Tool 和当前活动 API 保留兼容。旧变更 Tool、重规划/唤醒管理 API 当前明确拒绝。legacy 模式注册旧组件，并在正式边界限制为只读命令/Tool；不伪造新版 LifeState。

## 配置与数据

| 配置 | 说明 |
|---|---|
| `engine.mode` | `life` 或 `legacy`，不能在线直接切换 |
| `instance.writer_lock` | 默认自动使用数据目录锁；已有权威记录沿用原锁，非空值为绝对路径覆盖 |
| `instance.allowed_streams` | 空列表跟随 Host/适配器接入范围；非空为精确白名单，禁止通配符 |
| `model.task_name` / `model_name` | Host 路由任务与可选模型名 |
| `model.generate_on_start` | 每次加载请求一次决策，默认 false |
| `model.daily_budget` | 协调器 24 小时请求预算，默认 8 |
| `model.timeout_seconds` | 单次请求上限，默认 25 秒 |
| `legacy` | 旧配置 1 的显式嵌套配置，仍需管理员；频率写入关闭 |

SDK 授予的数据根中，`life/maivita.db` 与 `legacy/goals.db` 分离。`engine-authority.json` 指定唯一生效引擎，切换票据绑定源 revision、目标配置、共享锁与回滚快照。新旧调度器不能并行。

## 备份与回滚

离线切换使用 `tools/switch_engine.py`，先取得共享锁、备份已有引擎库并校验，再生成新配置与票据；不覆盖源配置或自动启动 Host。回切需要新的反向票据，不将新库改名交给旧引擎。迁移只接受显式离线快照，歧义行保留并阻止自主激活。

参阅[安装与数据导入](docs/INSTALL.md)、[回滚](docs/ROLLBACK.md)、[已知限制](docs/LIMITATIONS.md)和[接口契约](docs/CONTRACTS.md)。

## 源码导航

运行实现统一收拢到 `maivita/`，根目录保留 SDK 入口、配置与维护工具：

| 分类 | 路径 |
|---|---|
| 宿主适配 | `maivita/host/`：生命周期、模型路由、配置与引擎权威 |
| 新生活引擎 | `maivita/life/`：领域、状态、规划、协调、连续性、事件与存储 |
| 旧兼容引擎 | `maivita/legacy/`：旧日程、固定睡眠及相应服务 |
| 共享与边界 | `maivita/shared/`、`maivita/compatibility/`、`maivita/integrations/` |
| 验证与维护 | `tests/`、`integration_tests/`、`tools/`、`.github/`、`docs/` |

旧引擎仅在受控选择后执行；部分外部端口只有隔离测试，真实传输仍关闭。完整目录树和启用边界见[源码目录说明](docs/SOURCE_LAYOUT.md)。

## 验证

```text
python -m pip install -r requirements-i0.txt
python tools/verify_ready.py
```

CI 无凭据运行静态、单元及固定原始 Runner 检查；完整 Host 与有限真模型在受控本地实例单独验收，不把 CI 通过冒充模型或生态验收。内部设计、工作计划、提示词和过程报告不随仓库及安装包分发。

## 鸣谢与许可

感谢 [MaiBot](https://github.com/Mai-with-u/MaiBot)、[自主规划插件](https://github.com/DavidBlackCN/maibot-autonomous-planning-plugin)、[xuqian13](https://github.com/xuqian13/autonomous_planning_plugin) 和 [cateyemizuki](https://github.com/cateyemizuki/autonomous_planning_plugin)。保留原作者署名与 [AGPL-3.0](LICENSE)，完整来源见 [SOURCES](docs/SOURCES.md)，版本变化见 [CHANGELOG](docs/CHANGELOG.md)。
