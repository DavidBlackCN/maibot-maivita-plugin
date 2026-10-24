# MaiVita 跨插件接口契约 1.0.0

状态：冻结 v1 字段保留，特权传输与推送订阅不启用；新增公开拉取接口用于隔离联调。声明 API 为 get_integration_contract@1，生活查询 API envelope 为 api_version=1；数据库 Schema 4 和配置 3 独立版本化。机器形状由 integrations/contracts.py 导出 JSON Schema，未知字段拒绝，重大不兼容变更新增主版本，不静默改变 v1。

修复候选仅支持 Host 1.3.12 / SDK 2.15.0。source_stream 投影要求非空匹配来源；活动、计划条目、目标的新命令不再接受 source_stream，因为 schema 4 无可信来源字段。已有行仍可加载并被公开读取过滤；没有批量改公开或数据库迁移。

get_public_events@1 是无副作用的公开拉取接口，参数 after_version / after_index 为游标，limit 为 1..100。返回事件数组、扫描游标和 as_of；即使遇到过滤或过期事件也推进扫描游标。事件摘要由固定白名单产生，绝不透传内部 payload。消费者应将接收记录与游标放在自己的同一事务中。它不创建订阅、不确认 Vita 的内部 outbox、不授权事件参数中的生产者；协调层从固定全名 API 读取以绑定来源。

get_public_route_scope@1 只检查给定 stream_id 是否由当前 life 引擎守卫覆盖，返回 character_id、guarded 和活动配置摘要；不是用户身份或发送许可。legacy/not-ready 均返回不可用。

SocialEvent / SocialAck 通过 from_wire 的 JSON-aware 严格解析处理 ISO 时间，Python model_validate 的严格语义保持不变。SocialConsumer 校验 event_id/consumer_id，将 accepted/duplicate 转内部确认，expired 转过期，rejected 转失败终态。推送适配器仍是可测试端口，不开放任意远端注册。

生活 tick 每秒独立推进；模型和派发各只有一个受生命周期管理的后台任务。消费者单次等待默认两秒（上限五秒）、批次有界、原五次重试上限保留。上述延迟预算假设事件循环可运行及调用遵守异步取消；无法保证阻塞事件循环的第三方代码。

| 能力 | 冻结类型 | 运行状态 |
|---|---|---|
| Owner Auth | IdentityRequest / IdentityDecision | H03 缺失，disabled；proof_ref/context_ref 必须来自真实可信传输，JSON 校验不构成认证 |
| 统一策略 | PolicyRequest / PolicyDecision | disabled；身份不替代策略，缺失/超时/过期/拒绝均拒绝特权动作 |
| 主动社交 | SocialEvent / SocialAck | disabled；仅 public_summary，event_id 幂等，过期不补发，不写对方数据库 |
| 可信回执 | H01 | unsupported；必须由 Host 绑定插件/用途/attempt/stream/有效期和一次性消费 |
| 归属频率 | H02 | unsupported；需要 Host+SDK 的 owner-bound 设置/撤销及断连清理 |

身份请求带 request_id、宿主不透明 context_ref、action、character_id、stream_id。响应绑定 request_id/stream、issuer、subject/proof 引用、带时区签发/到期时间，最长 30 秒；拒绝普通参数构造身份。统一策略请求再绑定 state_version/config_revision，响应带同一请求和版本、allow/deny 原因、到期时间。未来适配器必须检查调用插件身份、可信 issuer 与这些绑定，不能仅通过解析模型获得权限。

社交事件带 contract_version、event_id、character_id、event_type、state_version、event_index、occurred_at、recorded_at、expires_at、visibility、最小摘要。按角色 state_version/event_index 顺序，至少一次投递；消费者持久去重同 event_id，返回 accepted/duplicate/expired/rejected。重试沿用原 ID，未知结果不能生成新社交机会，过期不发送。生活事实不等待消费者成功；失败留待诊断。

公开查询 get_life_state/get_plan/get_availability/get_diagnostics/get_current_activity 只返回 public_summary；旧完整当前活动 alias 读同一被选引擎。特权 request_replan/request_wakeup 与旧变更 Tool 在两个模式均明确拒绝。engine/contract 状态 API 不触发模型。legacy 模式保留新查询的静态绑定以满足原生 WebUI 校验，调用返回 ENGINE_UNAVAILABLE；只运行旧引擎，不伪造新生活状态。

当前没有绑定任何外部 API 路由、授权令牌或回调注册入口。联合阶段先协商版本、声明可信来源与最小能力，再实现单独适配器并复验；本项目不改 Host、Owner Auth、策略或社交插件来制造接口。错误包括 PERMISSION_DENIED、POLICY_UNAVAILABLE、NOT_READY、RELOAD_REQUIRED、STALE_STATE、IDEMPOTENCY_CONFLICT，默认不允许自动权限回退。
