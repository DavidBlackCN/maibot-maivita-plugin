# MaiVita I1 开发候选安装

仅供全新隔离测试实例。manifest 版本 0.2.0（Host 只接受三段数字，开发通道由名称/包名标明），Host v1.3.12 / commit 5c7637e6913c84d1c3d5e742fa9e95a7e70d3501，SDK 2.15.0，Python >=3.12；本轮实际测试 Python 3.13.6。正式首次安装门禁以 I1_REPORT.md 为准，尚未验收的 staging 目录不能当作已通过发布包。

1. 完整 Host 安装在 `D:\Dev\maimai-plugin\maibot`。本轮测试环境为 MaiVita 项目的 `.integration/i1-host-env`，独立实例为 `.integration/i1-instance`；原 Host 的 `.venv` 仍为官方锁定 SDK 2.12.0，不能混用。不能使用生产实例的数据、配置或账号。
2. 官方 uv.lock 锁定的 SDK 可能不是最新版本；安装锁定依赖后须明确安装 SDK 2.15.0，并记录覆盖。不要修改 Host pyproject/uv.lock；再次 uv sync 会恢复其 SDK 锁定版本。
3. 在独立实例的插件目录安装唯一 MaiVita 候选。先停止并移除旧完整规划插件与 R0 MaiVita；同 ID 不能共装，旧源码保留在别处。保持旧实例停止，不自动从旧库迁移。
4. 使用本包 config.example.toml 创建**新的**插件配置，保留 `config_version="2"`（Host 要求可按数字比较，R0 为 1，不能仅改版本转换旧配置）。默认 disabled。显式填写 instance.acknowledgement、实例级绝对 writer_lock 和真实测试 stream IDs；不能使用通配符。锁路径应由该独立实例所有旧/新版本共用；不要手工删除运行中的锁文件。
5. 配置 Host 专用测试模型，给 model.task_name（默认 utils），可选明确 model_name。不得从生产配置复制密钥。模型只通过 SDK llm.generate 调用，插件不持有凭据。配置 `model.generate_on_start=true` 后，每次加载可请求一次自主决策，计入 24 小时预算；默认关闭，超时/非法结果不自动重试。
6. 用户确认 Host EULA/隐私条款后启动；测试实例关闭遥测、自动更新、非测试平台接入。不得伪造用户已同意记录。
7. 检查 get_diagnostics、公开状态/计划/旧 API、Tool、Hook、单写者、重载和恢复。通过 ACCEPTANCE.md 全部首装检查及真实模型后才能生成干净 ZIP。

本工作区可用 `tools/run_i1_host_smoke.py --agreements-confirmed --exercise-model` 进行受控启动/真实模型/API/启停验证（会停止 Host）；`tools/run_i1_components.py` 调用完整 MainSystem 的原始初始化、调度及组件执行路径进行本地测试。运行前不得已有同实例 Host。协议参数仅在操作者已明确同意后使用。测试配置在 `.integration/i1-instance/config/model_config.toml`，含密钥的该文件必须留在忽略目录，不能随包分发。

完整 Host 初次启动入口以其 CLI 为准；本包不修改 Host 启动流程。主库在 SDK 注入 data_dir/maivita.db，第二个 runtime 锁也在此目录。发现 goals.db 拒绝加载，已有新库只校验打开，不自动升级/降级。配置变化先停 Runtime，需显式重载；disabled 配置会卸载，不保留旧 worker。

`/maivita status|plan|availability|diagnostics` 和 `/plan` 的只读默认状态使用正常 send.text，遵守完整 Hook/能力检查；睡眠时无管理回执例外，命令不会唤醒。API 全为 public_summary；所有管理写入暂拒绝。详见 LIMITATIONS.md。
