# MaiVita 跨插件接口契约 1.0.0

状态：冻结 v1 字段保留，特权传输与推送订阅不启用；新增公开拉取接口用于隔离联调。声明 API 为 get_integration_contract@1，生活查询 API envelope 为 api_version=1；数据库 Schema 7 和配置 4 独立版本化。机器形状由 integrations/contracts.py 导出 JSON Schema，未知字段拒绝，重大不兼容变更新增主版本，不静默改变 v1。

兼容范围由 Manifest 声明，接口 host/sdk 返回实际运行版本；已验证组合包含 Host 1.3.19 / SDK 2.20.0、1.3.18 / 2.19.0、1.3.15 / 2.16.0、1.3.12 / 2.15.0。source_stream 投影要求非空匹配来源；活动、计划条目、目标的新命令不再接受 source_stream，因为 schema 4 无可信来源字段。已有行仍可加载并被公开读取过滤；没有批量改公开或数据库迁移。

get_public_events@1 是无副作用的公开拉取接口，参数 after_version / after_index 为游标，limit 为 1..100。返回事件数组、扫描游标和 as_of；即使遇到过滤或过期事件也推进扫描游标。事件摘要由固定白名单产生，绝不透传内部 payload。消费者应将接收记录与游标放在自己的同一事务中。它不创建订阅、不确认 Vita 的内部 outbox、不授权事件参数中的生产者；协调层从固定全名 API 读取以绑定来源。

get_public_route_scope@1 核对给定 stream_id 是否在配置范围内，再通过正式 Host chat.get_all_streams 核对唯一的 QQ 会话及账号/scope。保留 stream_id、character_id、guarded、config_digest，补充 platform、bot_account_id、route_scope、as_of、snapshot_valid_until 和 send_permit=false。消费者必须对照入站路由，不能只按群号猜测。时间为带时区 ISO 字符串，快照期限最长 5 秒，期间也可能提前失效。

未就绪或读取期间实例/配置变更返回 ENGINE_UNAVAILABLE；无效 stream_id 返回 INVALID_ARGUMENT。ROUTE_UNAVAILABLE 的 error.reason 区分 OUTSIDE_CONFIGURED_SCOPE（明确被当前配置排除）与 HOST_ROUTE_UNRESOLVED（未找到唯一合法 Host 路由、RPC 失败或超时）。后者不是“确定不受管”，不可据此放行。查询不枚举会话，不返回用户或管理员名单，不授予管理或发送许可。Host 查询有 2 秒超时；消费者仍须设置整体超时和并发上限。

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

公开查询 get_life_state/get_plan/get_availability/get_diagnostics/get_current_activity 只返回 public_summary；旧完整当前活动 alias 读取同一 life 引擎。特权 request_replan/request_wakeup 与旧变更 Tool 均明确拒绝。engine/contract 状态 API 不触发模型。查询的静态绑定满足原生 WebUI 校验；get_engine_status 的 engine 固定为 life，ready 表示实际加载状态。

get_availability@1 保留原有字段，新增 `state`（free/slow/busy）、`reason`（显示原因）、`enforcement_enabled`（忙闲守卫开关）。私密活动只提供通用原因，不暴露活动名或类别。睡眠会话优先，其次身体极值，再按类别及能耗率推导；推导不受开关影响。启用守卫后 busy 使 available/can_reply_recommendation/can_proactively_contact_recommendation 为 false；slow 等同 free。关闭守卫保留原有睡眠可用性判断。以上均为 Vita 的状态建议，不是发送许可，也不替代 MaiHush；状态查询不写数据库、不唤醒、不补发消息。

可用性 data 中 `valid_until` 仍表示预计的睡眠/活动检查截止时间（可能为 null），不是缓存许可。新增 `as_of` 与 `snapshot_valid_until` 表示本次读取时间与最长 5 秒的快照期限，若已知截止更早则取更早值；已过期的活动状态不会因此获得新的有效期。三者均为带时区 ISO 字符串。`send_permit` 始终为 false。sleeping 与 temporarily_awake 都不推荐回复；忙碌开关不改变睡眠守卫。读取不唤醒、不生成活动、不返回私有活动内容。

路由与生活是两个独立查询，不构成原子发送事务；生命期切换时不返回旧实例的查询结果。消费者应在生成前和发送前检查路由、状态及新鲜度，最终仍走受控发送链。直接调用适配器 send_poke 不经过普通发送守卫，双次查询也无法消除最后提交窗口。未完成适配的 Smart Poke 不会因为本接口增强而自动受到保护。

官方有限管理命令读取 MaiIdentity 的 get_vita_policy@1，状态展示读取 MaiHush 的 get_public_constraints@1；这些查询不依赖 MaiCoordinate。通用特权传输不绑定授权令牌或回调注册入口，不通过公开查询获得执行权限。错误包括 PERMISSION_DENIED、POLICY_UNAVAILABLE、NOT_READY、RELOAD_REQUIRED、STALE_STATE、IDEMPOTENCY_CONFLICT，默认不允许自动权限回退。


默认会话范围：Vita 的空 allowed_streams 表示所有 Host 接入会话，非空仍精确限制；Identity 的空 route_scope 仅匹配消息 scope=None。自动实例标识、自动锁和默认启用均不建立可信调用者身份，不开放 H01/H02/H03 或跨插件特权。

`get_public_life_context@1` 在同一读取视图返回当前公开活动、地点、情绪、睡眠、忙闲、身体估计、查询时间，以及最近 24 小时最多 20 条公开活动。私有活动与聊天不进入素材；连续性未知区间保留标记。活动结束不是目标完成证明。继续活动和身体积分不会重复生成公开活动开始事件。
