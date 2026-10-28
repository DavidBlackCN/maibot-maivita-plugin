# MaiCoordinate 0.3.1

复用现有协调层，本轮组合为 MaiVita 0.5.1、MaiHush 0.2.2、MaiIdentity 3.2.1。支持精确 Host 1.3.12 / SDK 2.15.0 和 Host 1.3.15 / SDK 2.16.0。默认启用 shadow，只读事实和记录影子决策，不主动生成或发送。可维护源码及联合 CI 收录于 MaiVita 仓库 ecosystem/coordinator；工作区同名目录为交付工作副本。

安装后由 Host 生成配置。在适配器已允许的测试群发一条消息，协调层会把 Host 接收路由与 Hush 公开绑定逐项核对后记住该群，最多四群；无需填写 instance_id、scope 或内部 session ID。空 scope 精确匹配 None。新发现群不会补发绑定之前的旧醒来事件。使用 `/maicoordinate status` 查看状态。

需要主动表达时设置 `mode = "controlled"`，选择 Host 中已有的 `model_task`（默认 utils），可填写 `character_context` 人设提示，再重载。会产生模型调用和普通群消息；仍受 Vita 睡眠、Hush 静默、冷却、预算、去重和发送前重检控制。它与 Vita 共享官方 Host 的 nickname、personality、behavior_style、reply_style；character_context 仅补充表达风格。

保留显式 routes 精确白名单。旧显式绑定不会自动改为新路由；修改绑定应按下文回滚/迁移边界处理。配置热更新先停止流程并要求重载；自动发现路线可在原数据目录恢复。错误路由不默认放行。

## 流程与边界

- 仅从固定插件全名的 get_public_events@1 拉取；不提供外部推送入口，事件 JSON 不能自报可信生产者或目标。
- 醒来、活动开始/结束、计划调整可创建表达候选；social_event_types 可限制范围。每条路由合并积压机会，不补发过期事件。只使用公开生活事实、当前群关系摘要和最近三次已发送事件类型，不编造长期记忆。
- 接收事件、幂等摘要、消费游标在同一事务中持久化；同事件同路由不重复执行，内容变化冲突。
- 缺失、过期、legacy 模式、错误封套、路由错配、未知状态、时钟回跳均拒绝新动作。Hush 必须 READY/NONE/NORMAL；Vita 必须 awake/winding_down。
- 生成前持久预留冷却，限制失败模型调用的频率；发送前写 executing 并延长冷却。宕机或响应丢失保留 unknown，绝不自动重发或退款。生成中重启则取消；新事件也受持久冷却约束。shadow 检查相同冷却，但不消费冷却。
- 每次快照（包括生成后的重检）重新核验接口发现、契约、Host/SDK 与必要功能标志（允许满足契约的补丁升级）。单个路由失败或卸载时，先取消并等待所有所属任务退出，再关闭数据库。
- Host 的 sent 只证明至少一次送达，记录 sent_some；不能据此判断全量送达或已读。sent=false 记 not_sent，当前宿主无法细分守卫拒绝和平台失败，因此不编造该分类。
- API 单次两秒、生成八秒、发送十秒；单路由一个流程，最多四路并发，队列上限 1024。数据库操作不跨 await，外部等待均有超时。协作式取消之外的事件循环阻塞不在延迟保证内。
- send.text 仍受 Hush/Vita 正常发送守卫约束；不提供静默回执例外、唤醒或解禁。后置 Hook 改路由、守卫卸载/断连、其他平台出口仍属于宿主限制。

## 公开只读 API

| API @1 | 行为 |
| --- | --- |
| get_status | 模式、健康、状态计数、Host/SDK 和能力限制 |
| get_snapshot(route_id) | 当前三方快照、版本、有效期、确定性原因；send_permit 永远 false |
| get_action_result(route_id,event_id) | 稳定事件的状态、原因、读取版本、attempt 与更新时间；不暴露正文 |

H03 未通过，特权方法不存在。关系、owner/proof/caller 参数不会获得权限。H01/H02 同样不支持。

## 存储维护

默认保留事件正文七天，可配置 1～90 天；终态到期后清除正文，保留摘要、状态、原因、版本、attempt 和更新时间，以便继续对账和识别同 ID 不同参数。pending/preparing/executing 不被清理。维护同时清理到期冷却并执行 WAL checkpoint，不读取其他插件数据。

记录数默认上限 10,000，可配置 256～100,000，且不得小于 queue_capacity。上限包含已压缩的回执；达到上限时事务不接收新事件、不推进游标，仍处理已持久化的 pending，状态为 DEGRADED/STORAGE_FULL。不会为腾空间删除去重记录或重置 unknown。SQLite 文件重用空闲页，不保证操作系统文件即时缩小。

容量耗尽需停机备份整个数据目录并审核后提高预算（不超过上限），或另行设计保留去重的归档迁移。本候选没有自动迁移/删除旧账本入口。不能用删除库后重建的方式恢复生产者旧事件；切换路由也需独立离线迁移。

WebUI 路由编辑器可以展示空白草稿，但空白草稿无法通过实际配置校验。config.toml 是明确的运维配置；配置热更新会停止服务并要求重载，不在活动任务中改绑路由。

## 验证

单元：`python -m pytest tests -q`，在可写的独立临时目录运行。工作区联合验证：`python tools/run_joint_native.py --output .test-runs/<新的目录>`，使用已装固定 Host 依赖的 Python；脚本校验 Host/SDK 源码摘要，隔离配置、禁止非回环网络，真实三插件和协调插件经原生 Runner/SendService 执行，模型与最终发送驱动为合成实现。

本版包含协调层回归与 20 项原版 Host 联合场景；联合脚本也支持 `--bundle <候选包目录>` 从校验过的 ZIP 安装验证。完整工作区回归与证据索引见根目录 COORDINATION_DELIVERY.md。

此源码候选没有真实 QQ、真实模型语义、长期运行或生产验收。保留数据目录和工作流记录即可停止/回滚插件；回滚不得将 unknown 清成 pending。


### 2026-10-28 WebUI 配置兼容修复

修复空字符串枚举导致的 Select.Item 崩溃，以及必填账户条目无法生成编辑 Schema。历史确认字段不再显示为空选项；固定关闭的特权字段只读显示。运行时模型和权限校验不变。高级嵌套角色/旧引擎表请使用原始 TOML 编辑，避免 Host 表单把对象转成字符串。

若 Identity 页面提示缺少 plugin.config_version，在现有 [plugin] 内补入 config_version = "3" 后保存并重载，保留原有其他配置。Vita 使用 "3"，Coordinator 使用 "1"。这些是配置格式版本，不是插件发布版本；不能填写 3.2.1 或 0.5.1。不要删除数据或只保留 enabled。当前源码真实模型 I1 和 QQ 验收仍待进行，不沿用旧版本通过记录。
