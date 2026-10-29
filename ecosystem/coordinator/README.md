# MaiCoordinate

> **这是可选扩展，不是核心组件。**
> 三核心插件（MaiVita / MaiIdentity / MaiHush）不依赖它，不安装也能正常使用。
> 默认 shadow 模式不产生任何模型调用或消息发送。

根据公开生活事件协调受控主动表达；需要这项功能时再安装，并显式开启 controlled 模式。

- 当前版本：`0.3.1`
- MaiBot 版本范围：`1.3.12–1.3.15`
- Plugin SDK 版本范围：`2.15.0–2.16.0`
- 项目地址：[MaiVita / ecosystem/coordinator](https://github.com/DavidBlackCN/maibot-maivita-plugin/tree/main/ecosystem/coordinator)

仅支持精确配对 **Host 1.3.12 / SDK 2.15.0**、**Host 1.3.15 / SDK 2.16.0**，不支持中间版本或交叉配对。配合 MaiVita 0.6.2、MaiIdentity 3.2.2、MaiHush 0.2.2 使用，无需私有 Host 补丁。

## 功能

### 影子观察

默认 `mode = "shadow"`，读取三核心公开状态并记录影子决策，便于查看路由与约束是否就绪。不会调用模型或发送消息；观察模式仍会轮询公开 API 并保存本地账本。

### 受控主动表达（可选）

显式设置 `mode = "controlled"` 后，可针对醒来、活动开始、活动结束和计划调整生成表达。使用公开生活事实、当前群公开关系摘要、最近三次已发事件类型及 Host 人设，不把计划当成已发生经历，不编造长期记忆。

默认每群一小时冷却；积压事件合并为当前机会，过期事件不补发。生成前预留冷却，发送前重新检查生活、静音、路由、人设和关系版本；模型失败不立即退款重试，发送结果未知不自动重发。

### 已管理群自动发现

仅核对 Host 接收路由与 MaiHush 已管理群的公开绑定，最多四群，不自动接管其他群。首次发现群不会补发绑定前的旧事件。也支持显式精确路由白名单。

## 安装

### 1. 按需放置插件

先配置好三核心；只需要生活、身份与静音功能时，无需安装本插件。需要主动表达协调时，将唯一一份插件放到：

```text
MaiBot/
└── plugins/
    └── maibot-maicoordinate-plugin/
        ├── _manifest.json
        ├── plugin.py
        └── maicoordinate/
```

可维护源码收录于 MaiVita 仓库的 `ecosystem/coordinator`，工作区同名目录是副本，不是另一套插件。请使用 MaiBot 自己的 Python 环境。

### 2. 先以 shadow 检查

首次加载由 Host 生成配置。保留 `routes = []` 和 `mode = "shadow"`，在 Hush 已管理群发送一条消息，再用 `/maicoordinate status` 查看模式和发现群数。通常无需填写实例 ID、scope 或内部会话 ID。

未发现路由时先核对 Hush 路由和当前接入群；空 scope 只匹配 Host `scope=None`，不是通配符。三核心不可用、契约不匹配或状态不明确时拒绝新动作。

### 3. 按需开启主动表达

确认 Host 已配置模型任务，将根级 `mode` 改为 `"controlled"`，`model_task` 填为已有任务名（默认 `utils`），保存并重载。**此模式会产生模型调用费用和普通群消息**。恢复 `shadow` 并重载即可停止主动生成与发送。

Vita 睡眠、Hush 静默及冷却仍会阻止表达，不提供唤醒、解除静音或静默回执豁免。

### 从旧版本更新与停止使用

停机备份代码、`config.toml` 和完整 SDK 数据目录，再替换代码。保留现有路由绑定与账本，自动发现路由可从原数据目录恢复。配置热更新会停止流程并要求重载，不能在线改绑已有路由。

不再需要时可停用或移走本插件，保留数据备份；三核心继续使用。回滚须成套恢复匹配的代码、配置与数据，不把 unknown 结果改回 pending，不删库重建或重放旧事件。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/maicoordinate status` | 查看运行模式、健康状态与路由情况；回复受正常发送守卫限制 |

## 配置

完整说明见 [config.example.toml](config.example.toml)。除 `[plugin]` 外，以下选项都位于 TOML 根级，须写在 `[plugin]` 前。

### 运行模式与模型

```toml
# 默认观察模式，不调用模型或发送；显式 controlled 才启用表达
mode = "shadow"
# controlled 使用的 Host 已有任务名，不在此填写密钥
model_task = "utils"
# 仅补充表达风格；仍读取 Host 的昵称、人设、行为及回复风格
character_context = ""
# 每路由冷却秒数，范围 60–86400
cooldown_seconds = 3600
```

### 路由与事件

```toml
# 默认空数组：发现并核对 Hush 已管理群，最多四群
routes = []
# 可选事件白名单；省略时默认包含以下四种
social_event_types = ["life.wakeup", "life.activity.started", "life.activity.ended", "life.plan.revised"]
# 轮询公开事件的间隔，不是模型调用间隔；范围 1–60 秒
poll_seconds = 5
```

显式路由需要 route_id、instance_id、adapter_namespace、bot_account_id、group_id、stream_id；character_id 固定为 `default`，platform 固定为 `qq`，route_scope 默认空且只匹配 `None`，binding_revision 默认 1 且为正整数。各字段须与三核心实际绑定一致，route_id 和 stream_id 不能重复；不要用空白草稿作为运行配置。

### 队列与账本

```toml
# 待处理事件容量，范围 1–1024，不小于显式路由数量
queue_capacity = 256
# 全部回执记录上限，范围 256–100000，且不小于 queue_capacity
max_records = 10000
# 终态事件正文保留天数，范围 1–90
payload_retention_days = 7
# 通用特权写入未开放，只能为 false
privileged_writes = false

[plugin]
# 插件开关与固定配置格式版本
enabled = true
config_version = "1"
```

终态正文到期清理，去重摘要、状态和结果仍保留；pending/preparing/executing 不清理。达到 `max_records` 后停止接收新事件并报告 STORAGE_FULL，仍处理已持久化的 pending。应停机备份并在上限内提高容量，不删除账本“恢复运行”；SQLite 不保证立即缩小磁盘文件。

## 公开只读 API

| API @1 | 说明 |
| --- | --- |
| `get_status` | 模式、健康、状态计数、Host/SDK 与限制 |
| `get_snapshot(route_id)` | 三方快照、版本、有效期及原因；send_permit 始终 false |
| `get_action_result(route_id,event_id)` | 事件状态、原因、版本和更新时间，不暴露正文 |

读取结果不构成发送或管理授权，不提供外部事件推送或特权写接口。

## 自 v0.3.0 以来的修改

- v0.3.1：支持活动与计划事件表达、共享 Host 人设和当前群公开关系摘要，修复配置界面兼容性。
- 文档明确 opt-in 定位，三核心不依赖协调层；保留既有 shadow 默认配置。

完整记录见 [UPDATE_NOTES.md](UPDATE_NOTES.md)。

## 已知限制

不支持 H01 静默回执例外、H02 频率所有权覆盖、H03 通用特权委托。Host 的 sent 只能说明至少一次送达，不能证明全部送达或已读。后置 Hook 改路由、守卫卸载和绕过 Host 的直接平台发送不在保证内。时钟回跳、未知状态或存储错误时不猜测恢复。

## 鸣谢

感谢 MaiBot、插件 SDK 与 MaiVita、MaiIdentity、MaiHush 的公开接口。

## 许可

[MIT](LICENSE)。
