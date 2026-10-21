# MaiVita Integration-Ready 候选安装

仅用于独立空实例和后续超级 MaiBot 核心插件联合测试，不接管生产实例。版本 0.3.0、配置 3、数据库 Schema 4、契约 1.0.0。固定 Host 1.3.12 / `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`、SDK 2.15.0；Host 原 uv.lock 的 SDK 2.12.0 需在独立环境明确覆盖，不改 Host 锁文件。

1. 停止旧完整规划插件，安装本包的唯一 MaiVita ID。保留旧代码和数据库，不同版本不得同时运行。隔离环境须有 Manifest 声明的 pillow、pytz、tzdata；若 uv 创建的环境没有 pip，先用 uv pip 在该环境安装这些依赖，不能假设 Host 自动安装必定成功。
2. 从 config.example.toml 创建新配置，默认 disabled。填写 instance.acknowledgement=`isolated-test-instance`、全实例共用的绝对 writer_lock、明确 allowed_streams；禁止通配符。
3. 默认 engine.mode=`life`。模型通过 Host 专用配置的 utils 任务路由；model.generate_on_start 默认 false，设 true 后每次加载最多请求一次，预算与超时生效。后续显著事件仍由已有协调器按预算调度，不定时轮询模型。
4. 配置版本 1/2 不自动升级。旧版 1 字段可放入 legacy 部分，但必须重新检查管理员和隔离目录；engine.mode=legacy 需要明确管理员，旧开放管理员语义拒绝。
5. 数据根由真实 SDK paths.data_dir 注入；life/maivita.db 与 legacy/goals.db 分目录，根 engine-authority.json 指定唯一权威。根下发现未分目录旧库时拒绝启动，不能手工改名搬入绕过审查。
6. 启动前按 Host 自身流程同意 EULA/隐私条款，隔离实例关闭遥测、真实平台接入、MCP 与 render。先验 API、Tool、Hook、单写者及重载，再进入联合测试。

跨引擎切换先禁用/停止 Host，保存当前配置副本。在相同环境安装 tools/requirements.txt 后执行：

```text
python tools/switch_engine.py --config <绝对配置路径> --data-dir <SDK授予数据根> --target legacy --output <新的配置副本> --isolated-instance
```

工具取得同一 writer_lock，保存所有已有引擎库的一致快照及 checksum，产生绑定源 revision、目标模式及完整配置摘要的切换票据；不改原配置、不启动 Host。检查输出后替换配置并显式重载。切回 life 同理，需要新的票据；不能只改 mode。首次空实例可直接激活指定的一个引擎。

旧库导入仅针对已验证的离线快照，使用 stage_legacy.py 的显式 snapshot/target/source-id/timezone/writer-lock 和 --development-staging；目标为 life/maivita.db，旧引擎必须停写。歧义行保留并阻止自主激活，不猜测约定或真实历史。详情见迁移计划及 ROLLBACK。

## 复现完整 Host 验证

先在虚拟环境安装 requirements-i0.txt，运行 `python tools/setup_i0_host.py` 下载并校验固定公开归档；然后 `python tools/setup_i1_host.py --destination <新的绝对空源码目录>` 解出完整 Host 和来源锁。工具不接受非空目录、不复制运行配置或 Git。

在新的独立 Python 环境用 uv 按 Host 原 uv.lock 执行 `uv sync --frozen --no-dev --no-install-project`（UV_PROJECT_ENVIRONMENT 指向该环境），再用 uv pip 明确安装 SDK 2.15.0 及 Manifest 中的依赖。不要在已有生产环境安装或改锁。

用 prepare_ready_instance.py 的 --host/--python/--instance 和可选 --test-model-config 指定仅测试配置，实例必须是本工作区 .integration 下的新目录。准备工具强制该自动验收实例的 daily_budget=1，包含不确定结果；重新加载不能额外消耗请求。

设置 MAIVITA_HOST_SOURCE、MAIVITA_HOST_PYTHON、MAIVITA_TEST_INSTANCE 为对应绝对路径，再运行 `tools/run_i1_host_smoke.py --agreements-confirmed --exercise-model`。协议开关仅在操作者确已同意后使用。它将通过真实认证验证 API/重载并停止 Host。之后 `tools/rehearse_ready.py --instance <实例绝对路径>` 运行原生组件、新旧回切和恢复；不会自行补足模型预算或重试未知结果。

tools/verify_ready.py 和 CI 不需要私有配置。audit_ready.py 另包含本工作区历史来源及相邻只读项目的校验，依赖相应本地历史证据；这些非公共工作记录不随 Git 分发，不能用缺失记录冒称来源已验收。
