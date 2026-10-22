# 源码目录与职责

从 0.3.1 起，运行实现统一放在 `maivita/`。此次整理结构和资源路径，不增加生活模拟功能，配置 3、SQLite Schema 4 和契约 1.0.0 不变。

```text
maibot-maivita-plugin/
├── plugin.py                 # SDK 入口
├── maivita/                  # 全部运行实现
│   ├── host/                 # 生命周期、模型与配置适配、引擎权威
│   ├── life/                 # 新生活引擎
│   │   ├── domain/           # Clock、领域模型、命令与约束
│   │   ├── life_state/       # 状态机、查询、运行循环和恢复
│   │   ├── planning/         # 混合规划候选与补丁
│   │   ├── coordinator/      # 决策预算、触发器和结果提交
│   │   ├── continuity/       # 已有连续性领域操作
│   │   ├── events/           # 持久事件和 outbox 分发
│   │   └── storage/          # SQLite、仓储、迁移和共享数据安全
│   ├── legacy/               # 旧日程与固定睡眠兼容引擎
│   │   ├── plugin.py         # 旧引擎入口
│   │   ├── config_models.py  # 旧配置 1
│   │   ├── planner/          # 旧日程生成与调度
│   │   ├── services/         # 旧命令、Tool、注入等服务
│   │   ├── core/             # 旧模型、状态、校验与频率逻辑
│   │   ├── database/         # 旧目标与日程数据库
│   │   ├── handlers/         # 旧注入与异常处理
│   │   └── cache/            # 缓存实现源码
│   ├── shared/utils/         # 时间、时区、图片、日志和流过滤
│   ├── compatibility/        # 配置、旧日程/Tool 语义桥
│   └── integrations/         # 宿主安全、公开 API 与外部端口
├── tools/                    # 验证、打包、迁移与切换
├── tests/                    # 单元回归与公开 fixtures
├── integration_tests/        # 原始 Runner 与完整 Host 验证
├── docs/                     # 安装、接口、限制、验收与回滚说明
└── .github/                  # 无密钥 CI
```

## 入口与迁移关系

life 调用链为 `plugin.py → maivita/host/entrypoint.py → maivita/host/plugin.py → maivita/life/life_state/runtime.py`；legacy 调用链从同一路由转向 `maivita/legacy/plugin.py`。只启动被选中的一个引擎，权威记录、共享锁及数据目录规则保持不变。

原 `development/` 中参与运行的代码已归入 `maivita/host/`，打包实现移到 `tools/package.py`；旧独立 manifest/config/webui 归档在本地忽略目录，不作为第二入口分发。根目录清单、配置示例、webui、许可和图标仍保留。新旧路径对照见 [source-layout-map.json](source-layout-map.json)。

容易混淆的名称现在有了明确分组：`legacy/planner/` 是旧日程实现，`life/planning/` 是新规划领域；`legacy/database/` 访问旧库，`life/storage/` 管理新库并提供共享安全工具；`legacy/cache/` 是源码，不能当生成缓存删除。

## 是否全部启用

目录都有职责，但并非每个文件都在默认模式执行：

| 类别 | 当前边界 |
|---|---|
| `host/` 与 `life/` | 当前 life 运行路径；习惯学习、清理等配置关闭时不会因代码存在而启用 |
| `legacy/` | 选择旧模式才运行相应服务，管理写入仍被拒绝，不与 life 调度器并行 |
| `compatibility/` | 当前活动投影、配置转换及旧语义桥；部分仅供隔离验证或离线运维 |
| `integrations/life_api.py`、`safety.py` | 已接入公开 API、旧写者检查与安全边界 |
| `integrations/contracts.py` | 冻结消息形状；校验形状不等于认证或可用传输 |
| `integrations/authorization.py`、`behavior.py`、`memory.py` | 有隔离测试的可注入端口；真实 Owner Auth、归属调频和记忆插件未接通 |
| `tests/`、`integration_tests/`、`.github/` | 验证材料，不进入插件安装包 |
| `tools/` | 独立 CLI；安装包仅含明确白名单中的迁移与切换工具 |

H01 可信发送回执不可用，H02 归属频率撤销、H03 可信身份未具备，相应功能保持关闭或拒绝。详见[契约](CONTRACTS.md)与[限制](LIMITATIONS.md)。本轮没有依据“未直接调用”删除代码，保留条件导入、包导出、离线入口和来源材料。

## 本地目录

| 目录 | 内容与边界 |
|---|---|
| `.integration/` | 隔离 Host、依赖、配置及合成实例，可能含专用测试密钥；排除 Git 和安装包 |
| `evidence/` | 原始验收证据、整理前源码/报告快照；排除 Git 和安装包 |
| `dist/` | 生成的安装包，旧包保留；排除 Git |
| `.venv/` | 本地 Python 环境；排除 Git |
| `__pycache__/`、`.pytest_cache/` | 自动生成缓存；排除 Git |
| `.git/` | 仓库元数据 |

本轮不迁移已有实例或数据。旧 `legacy_plugin.py` 的原字节存于 `tests/fixtures/provenance/legacy_plugin.r0.py` 并继续校验哈希；执行版本只适配导入与插件根路径。旧调度器派生缓存改用已授予数据目录，未加载数据目录时拒绝构造。

当前验证范围见[验收说明](ACCEPTANCE.md)，操作回退见[回滚说明](ROLLBACK.md)。内部设计、任务计划、提示词及过程报告不随仓库或安装包分发。
