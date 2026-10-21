# R0-03 选择性补丁审查

比较输入：本机 v5.1.0 `6c936467cfde6339c9eea91bbf761e3cf0a21fdc`，fork 固定 `01ce890f9b577d48fd5e3eb6eca1572a4d27df68`。不是按“落后几个提交”全量合并。证据源码保存于 `evidence/fork/`；SDK 实测 2.15.0，公开宿主契约 SHA 为 `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`。

| 来源补丁 | 审查结论与实现 | 验证/限制 |
|---|---|---|
| `c3618719cf1baac974a52753a8b92a4cb76a905d`，4.6.1 Hook | 采用并改写：兼容 `items` / `messages`，ContextItem 元数据、唯一 ID、逻辑回合继承；按原键回写；完整 kwargs 回传 | T03 双载荷、原对象不变、未知字段保留；真实 Host 整体替换 merge 代码参与测试。实际 ContextItem 反序列化器未实机验证 |
| 同上，replyer 合作注入 | 采用追加 upstream `extra_prompt`；修复 fork 显式参数 `attempt` 可能漏回的问题；新增自己的块标记，已有块或重试不重复注入 | T03 两插件组合、重复载荷、attempt=2、SDK 异常安全跳过 |
| `bf00c094eb8c1461c3e9fcfe6e6db0823f175ff5`，4.9.0 目录 | 改写采用：SDK 持久目录、显式初始化 GoalManager、日志和图片统一目录；缺能力时仅允许显式绝对配置 | T01、真实 SDK PluginPaths 和 T05 生命周期。拒绝源码目录回退 |
| 同上，自动复制旧 DB/WAL/SHM/日志 | 不采用自动复制；改为显式发现、只读预检、SQLite backup、一致恢复 | T01/T02；只操作合成 fixture；不自动激活 |
| 同上，权限 | 清理未用 `config.get_plugin`，保留本地实际使用的模型/消息/频率诊断能力；补 Pillow/pytz/tzdata 依赖和注册表只读能力 | manifest 回归与真实 SDK 代理；宿主授权尚未实机验收 |
| 同上，管理员空列表默认拒绝 | 新安装采用拒绝；旧配置转换显式保留 `legacy_empty_admin_all`，不直接套用 fork 的语义变化或 `is_local_operator` 假设 | T05 权限矩阵；R2 前待审 |
| 同上，命令引用兼容 | 选择性采用捕获式负向边界表达式，保留 `planning_cmd` 供旧服务读取 | 旧命令回归；其他 Tool 参数完全保留 |
| `ceaa7acc32f21039b67834631a761181ca92b4dc`，4.9.1 损坏自愈 | 拒绝损坏后删库/重建和隔离失败后删除。坏库、未知版本或结构停止启动 | T01 原始内容/版本不变；备份失败不继续初始化 |
| `4092f2c83c4e2c7ce5c5a83c0982347f6845c5b8`，send.forward | 本机已有声明与回退，保留 | 原公开输出回归 |
| `71612b77d77983218deb91d19afbbe5aa53b7cd0` 等历史超时修复 | 本机已有更完整模型路由、串行协调、不确定 RPC 超时拒绝盲重试；不降级覆盖 | 原 model_selection / generation_coordination 回归全部保留 |
| 4.6/4.7 新作息/无睡眠语义、移除 Tool/清理模块、4.8 图片重构 | 不吸收行为变化；R0 保留 v5.1.0 可观察日程、固定窗口、Tool 和图片逻辑 | 原 time_constraints / sleep_public_outputs / apply 回归 |

本轮额外安全修复：会话级回执 depth 替换成精确、一次性、10 秒令牌；全局频率恢复替换成要求 adjustment_id 的归属接口。**已审查宿主不具备这两个生产契约**，因此安全拒绝相关副作用，而不是伪造 SDK 能力。R0-06 尚未达到完整实机验收，详见报告。

旧完整 API 采用含点号 API 名称的精确匹配 alias。实际 SDK 组件元数据、Host APIRegistry、解析与调用代码共同执行通过；别名不创建第二个调度器或数据库。不需要复制旧 ID 的全功能插件；实际消费者/部署版本仍需联调。
