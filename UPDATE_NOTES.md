# 更新说明

## v0.9.0

- 统一每日参考计划与滚动实际活动，共用自然日模型预算；失败时有界延续或中性休息。
- 固定活动类别身体规则，清醒休息恢复；睡眠保持确定性截止时间。
- 新增公开生活素材 API、滚动事件投影及计划重新生成命令。
- 配置升级至 4，数据库升级至 7；新增副本升级、回滚与服务器验收工具。
- 固定 MaiTrace 6.3.0 适配与 Coordinate 0.4.0，支持独立空间发布和浏览互动，默认先观察。

## v0.8.2

### 修复
- **新安装用户的身体数值从未初始化**：energy/fatigue/stress 默认 None，
  而 advance 对 None 保持 None，导致「身体驱动日程」与「忙闲三态」实际不生效。
  新增 `[routine] initialize_body_state`（默认 true），首次启动初始化为 50，
  已有值不重置；关闭后保持未知。

### 调整
- 身体变化速率：睡眠 +8/-5/-3、工作学习 -3/+2/+0.8、其他 -1/+1/-1.0
  （原睡眠仅 -1/h 压力恢复过慢，工作 +1/h 增长过快）
- 数值异常观测：>95（stress/fatigue）或 <5（energy）时记 WARNING，
  `/maivita status` 显示异常提示；单次变化 >50 也告警。不自动修正。
- RULE_VERSION estimates-v2 -> estimates-v3

## v0.8.1

### 新增
- 忙闲三态（`free` / `slow` / `busy`）推导与显示，默认关闭
- `[availability]` 配置段：慢速活动类别、睡眠即忙、身体极值阈值
- `/maivita status` 显示当前三态与原因
- `get_availability` API 返回三态与是否可回复建议

### 说明
- 忙闲处理：`slow` 仅用于显示（等同正常回复），`busy` 通过
  发送守卫 abort（与原有睡眠静默并存，MaiHush 优先）
- `send_service.before_send` 只支持 continue/abort 且超时 1000ms，
  无法实现真正的延迟回复；半忙的延迟体验留待后续评估
- 不实现恢复后补发（`followup_after_busy` 仅支持 false）

## v0.8.0

### 新增
- 滚动分段（实验性，默认关闭）：角色有「此刻正在做什么」的实时状态，
  每 15 分钟一个决策窗，用概率压缩把模型调用控制在每日 20 次以内
- `[scheduler]` 配置段：决策间隔、变动概率、每日预算、分段时长上限
- `advance_segment` 命令：支持 continue（继续当前段）/ 换事 / 续做
- `SEGMENT_CONTRACT`：只决定「从现在开始的这一小段」，不排一整天
- 身体状态接入分段决策（疲劳 >=80 或精力 <=20 触发重新决策）
- `/maivita status` 显示当前活动、地点、已进行/剩余时间

### 修复
- **SQLite 3.37 STRICT+REAL 兼容问题**（生产阻塞）：
  系统 SQLite 3.37.2 下，STRICT 表 + BEFORE UPDATE 触发器 + REAL 列的
  UPDATE 会报 `cannot store REAL value in REAL column`，导致 energy/fatigue/
  stress 永远无法写入，身体状态始终为 NULL。
  改为 INTEGER 定点数存储（energy ×100、energy_rate ×1000、分钟取整），
  保留 STRICT 与触发器约束。系统 SQLite 下测试从 12 failed 变为 0 failed。
- 新增迁移 `006_integer_fixed_point.sql`（Schema 5 -> 6）

## v0.7.0

### 新增
- 每日规划改为生成 6-12 条带时间的日程表（fixed / window 混用），
  修复 0.6.2 只生成一句话待办的问题
- 新增计划质量诊断：全 unscheduled 或带时间条目不足 3 条时记录 degraded
- 新增 `[routine.weekly_pattern]` 周表配置（仅保留数据结构，尚未启用）

## v0.6.2

### 文档
- README 重写为面向使用者（功能/安装/命令/配置/限制）
- 新增 UPDATE_NOTES.md
- config.example.toml 全字段中文注释
- 删除重复文档：ACCEPTANCE/CHANGELOG/INSTALL/JOINT_RELEASE/PRODUCT_GUIDE

## v0.6.1

### 变更
- 生活管理员名单唯一来自 MaiIdentity 的 management.vita_admin_accounts。
- 移除 instance.owner_auth_enabled / privileged_writes_enabled；旧键无论真假均忽略，其他未知键仍报错。

### 兼容性
- 支持 Host 1.3.12 / SDK 2.15.0、Host 1.3.15 / SDK 2.16.0；配置版本 3，生活数据库 schema 4。
- 文档将 MaiCoordinate 定位为 opt-in 可选扩展；默认 shadow 行为未改变。

## v0.6.0

### 变更
- 退役 legacy 规划引擎；入口只运行 life，保留旧 API 投影、旧库离线导入及来源 fixture。

## v0.5.4

### 变更
- 默认启用每日规划，使用 Host planner 任务；增加模型路由选择、作息偏好与扩展状态展示。

## v0.5.3

### 变更
- 管理入口拒绝原因使用不含账号及正文的结构化日志。

## v0.5.2

### 变更
- 按适配器实际会话核对管理路由，兼容备用 Bot QQ 为空或过时。
- 修正 Hook 上下文呈现方式，计划列表展示取消和替换状态。

## v0.5.1

### 变更
- 修复 WebUI 空枚举和配置 Schema 展示，历史兼容字段不再显示为空选项。

## v0.5.0

### 变更
- 新增生活管理预览、确认及持久化结果，支持有限中文语法。
- 读取 Host 共享人设，为 Planner 提供公开上下文；提供公开事件供可选协调层消费。
- 增加联合验证与停机备份恢复工具。

## v0.4.x

### 变更
- 默认 life 安装不再要求手填部署确认串、写锁或会话 ID。
- 提供持久每日规划和官方 Host 有限管理命令，不依赖私有 Host 补丁。
- 兼容 Host 1.3.15 / SDK 2.16.0，同时保留原精确配对。

## v0.3.1 — 2026-10-22

- 将平铺运行模块收拢到 maivita/host、life、legacy、shared、compatibility、integrations，更新导入、CLI、CI 和打包白名单。
- 同步修正插件/图片资源根和旧模型配置定位；旧调度器派生缓存使用授权数据目录，未加载时拒绝构造。
- 保留原始 R0 入口的逐字节来源 fixture 和 SQL 001 哈希，配置、数据库与跨插件契约版本不变。
- 移除公开仓库和安装包中的内部架构、设计、工作计划、提示词及过程报告，保留必要使用说明。

## v0.3.0 — 2026-10-21

- 正式入口按配置 3 选择唯一 life/legacy delegate，新 Runtime 接入真实 SDK 生命周期、ModelPort、API/Tool/Command/Hook。
- 新增绑定配置、revision 和共享锁的离线切换票据，分离新旧数据目录并保留一致快照与回切路径；失败自清理，无自动旧引擎 fallback。
- 修复关闭时模型结果不确定后，持久触发器可能再次入队的问题：attempt 与批次认领原子提交，重启兼容收拢旧版取消间隙，不自动重试原触发器。
- 冻结 Owner Auth、统一策略与主动社交契约 1.0.0；H01/H02/H03 未具备时继续关闭或拒绝，禁用全局频率写入。
- 公开可移植测试源码、固定公开 Host fixture、CI、开发文档与操作说明，继续排除敏感配置和运行数据。

## v0.1.0 — 2026-10-21

### 新增

- 使用 MaiVita 名称、插件 ID 和独立配置版本 `1`。
- 按角色、规划、睡眠、模型、集成、存储和兼容设置组织配置，提供显式旧配置副本转换工具。
- 使用宿主持久化目录和共享写入锁，阻止新旧完整规划插件同时写入。
- 提供 SQLite 一致快照、dry-run 预检、校验和检查及独立目录恢复工具。
- 保留新旧完整当前活动 API 路由。

### 保留

- 基于自主规划插件 5.1.0 的日程生成、固定睡眠、当前活动注入和命令入口。
- 继承源码中的原作者信息、许可证和图标。

### 限制

- 未接通可信管理回执时，不提供睡眠静默发送例外。
- 缺少按插件归属撤销的频率接口时，不修改 Host 全局频率。
- Owner Auth 身份授权、自主睡眠和混合规划尚未接入当前入口。
- 不自动迁移旧运行库，不直接接管已有生产实例。

上游 5.1.0 及更早版本记录见 [自主规划插件 CHANGELOG](https://github.com/DavidBlackCN/maibot-autonomous-planning-plugin/blob/6c936467cfde6339c9eea91bbf761e3cf0a21fdc/CHANGELOG.md)。来源及继承关系见 [SOURCES](docs/SOURCES.md)。
