# 可选协调扩展与联合验证

核心组合为 MaiVita、MaiIdentity、MaiHush；MaiCoordinate 是 opt-in 可选扩展。三核心不依赖它，默认 shadow 不调用模型也不发送消息。

这里把已有 MaiCoordinate 纳入 MaiVita 仓库版本控制，没有创建新插件。`coordinator/` 是受版本控制的发布源码；工作区 `maibot-maicoordinate-plugin` 是交付工作副本。`coordinator-source.json` 记录全部分发源码与测试摘要。

联合 workflow 在官方 Host 1.3.15 / SDK 2.16.0、Python 3.12.15 下安装四个实际插件，经 Supervisor、Command、Hook、SDK、SendService 运行。模型和最终平台驱动为合成实现，不能据此声明真实模型或 QQ 验收。Host 与 SDK 文件逐一匹配来源锁；没有私有 Host 补丁。

`peers.json` 固定 Identity/Hush 的真实提交，避免任意远端分支变化使旧证据看似仍有效。更新对等插件后先运行它们的测试并提交，再执行：

```bash
python maibot-maivita-plugin/ecosystem/sync_from_workspace.py --workspace /path/to/super-maimai
```

该命令仅复制现有协调层白名单源码/测试及联合脚本，刷新对等提交，不复制配置、数据库、密钥或测试实例。提交本仓库后，`Super MaiMai joint contracts` workflow 自动验证准确的组合。若只改此目录中的协调源码，请先同步工作区交付副本，再制作最终包；必须检查 `coordinator-source.json` 与实际包一致。

本地可运行：`python tools/run_joint_native.py --host-source /path/to/pinned/Host --source-lock maibot-maihush-plugin/tests/fixtures/source-lock-1.3.15.json --output /path/to/new/evidence --auto-routes --trusted-management`。输出目录必须全新；`--trusted-management` 测试官方命令入口，不代表 H03 已实现。

## 文档更新与发布工具

README、UPDATE_NOTES 和 config.example.toml 的工作区副本与此目录同步维护。现有 sync_from_workspace.py 的白名单尚未包含 UPDATE_NOTES.md；文档重构后，旧 coordinator-source.json 中的 README 与配置示例摘要也不再代表当前文件。该文件保留作历史记录，不能作为当前文档的完整性证明。后续允许调整工具时，需更新白名单并重新生成摘要，再执行发布检查。

Vita 与 Identity 的旧打包工具仍引用已并入 README/UPDATE_NOTES 的文档名；发布前需更新打包文档清单。文档重构不修改这些工具，也不生成新包。
