# MaiVita

为 MaiBot 添加日程、日常活动和睡眠，让麦麦在聊天时有自己的生活背景。

当前版本 **0.9.0**，仍在开发中。

## 功能

- 根据 MaiBot 的人设安排日程，并随时间和身体状态调整活动。
- 聊天时参考当前活动、地点、心情和最近一天的生活记录。
- 模拟精力与疲劳，累了会休息，睡眠期间暂停回复。
- 支持管理员查看和调整日程、活动、目标及睡眠。

私聊中可以说“陪我聊会儿”。麦麦可能暂停手头的事，也可能边做边聊；被打断的活动可以稍后继续。睡眠期间，聊天邀请不会将它唤醒。

## 安装

目前支持以下版本组合：

| MaiBot | Plugin SDK |
| --- | --- |
| 1.3.18 | 2.19.0 |
| 1.3.15 | 2.16.0 |
| 1.3.12 | 2.15.0 |

1. 停止 MaiBot，将本插件放入 `plugins/maibot-maivita-plugin/`，确保目录内有 `plugin.py` 和 `_manifest.json`。不要同时运行其他完整的自主规划插件。
2. 在 MaiBot 使用的 Python 环境安装 `pillow>=9.0.0`、`tzdata>=2024.1`，并确认 `model_config.toml` 中已配置 `planner` 模型任务。
3. 启动 MaiBot，插件会生成默认配置。发送 `/maivita status` 查看状态，发送 `/maivita help` 查看用法。

**日程和活动生成会调用模型，产生相应费用。** 默认每天最多尝试 20 次，失败和重试也计入。

### 设置管理员

调整生活状态需要安装 MaiIdentity，并在它的 `config.toml` 中填写：

```toml
[management]
vita_admin_accounts = ["你的QQ号"]
```

保存后重载 MaiIdentity。名单为空时，无人拥有 MaiVita 管理权限；群管理员不会自动获得此权限。

## 常用配置

多数设置保持默认即可。可在 MaiBot 的插件设置界面修改，也可参考 [config.example.toml](config.example.toml)。

```toml
[instance]
timezone = "Asia/Shanghai"

[model]
# 补充角色的日常习惯，留空使用 MaiBot 人设
life_context = ""
daily_budget = 20
daily_planning_enabled = true
daily_planning_time = "08:00"

[routine]
preferred_sleep_time = "01:00"
preferred_wake_time = "08:00"
```

日程时间按 `timezone` 计算。作息时间是参考，角色不一定准点入睡或醒来。

关闭 `daily_planning_enabled` 只停止生成每日计划，日常活动仍会调用模型。需要完全停用时，关闭插件总开关。

如需指定其他模型，在 `[model]` 中设置 `mode = "model"`，并用 `model = "模型名称"` 指定 MaiBot 已配置的模型。密钥仍填写在 MaiBot 的模型配置中。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `/maivita status` | 查看当前状态和今日模型调用次数 |
| `/maivita plan` | 查看今日日程 |
| `/maivita activity` | 查看当前活动 |
| `/maivita goals` | 查看目标 |
| `/maivita replan` | 重新决定当前做什么 |
| `/maivita plan regenerate` | 重新生成今日日程 |
| `/maivita sleep 30` | 请求睡眠 30 分钟 |
| `/maivita wake` | 唤醒 |
| `麦麦管理 开始活动 阅读` | 开始一项活动 |
| `麦麦管理 结束当前活动` | 结束当前活动 |
| `/maivita plan add 明天 09:00 10:00 阅读` | 添加日程 |
| `麦麦管理 新增目标 学习Python` | 添加目标 |
| `/maivita help` | 查看更多管理命令 |

添加或修改日程、活动、目标，以及请求睡眠时，会先显示预览。按照提示，用同一账号在同一会话内发送 `/maivita confirm 请求ID` 确认，预览两分钟后过期。也可用 `/maivita cancel 请求ID` 取消，或用 `/maivita result 请求ID` 查询结果。

通过这些命令创建的日程、活动和目标是公开的，请勿填写私人信息。

## 常见问题

**为什么不回复？** 睡眠期间会暂停回复，命令回复也可能被拦截。管理员可用 `/maivita wake` 唤醒。如果同时使用 MaiHush，还需检查是否处于群静音中；唤醒不会解除群静音。

**“正在专注”会停止回复吗？** 不会。若希望身体过度疲劳时也暂停回复，可在高级设置中开启对应选项，默认关闭。

**日程一定会照着执行吗？** 不一定。它是当天的打算，实际活动会调整。生活记录和身体状态都是角色模拟，不代表机器人真的完成了现实中的操作。

## 可选扩展

[MaiCoordinate](ecosystem/coordinator/README.md) 可让麦麦根据生活状态主动发言；配合[适配版 MaiTrace](ecosystem/maitrace/README.md)，还可发布 QQ 空间动态、浏览和互动好友动态。安装与配置见各自说明。

## 其他资料

[更新说明](UPDATE_NOTES.md) · [排错](docs/DIAGNOSTICS.md) · [备份与回滚](docs/ROLLBACK.md) · [开发接口](docs/CONTRACTS.md)

## 鸣谢与许可

感谢 MaiBot、插件 SDK，以及 xuqian13、cateyemizuki 和 DavidBlackCN 的自主规划插件工作。来源与署名见 [SOURCES](docs/SOURCES.md)。

本项目使用 [AGPL-3.0](LICENSE) 许可证。
