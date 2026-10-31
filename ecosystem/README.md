# 可选协调扩展与联合验证

核心组合为 MaiVita、MaiIdentity、MaiHush；MaiCoordinate 是 opt-in 可选扩展。三核心不依赖它，默认 shadow 不调用模型也不发送消息。

这里把已有 MaiCoordinate 纳入 MaiVita 仓库版本控制，没有创建新插件。`coordinator/` 是受版本控制的发布源码；工作区 `maibot-maicoordinate-plugin` 是交付工作副本。`coordinator-source.json` 记录全部分发源码与测试摘要。

联合验收按需在工作区执行，安装四个实际插件，经 Supervisor、Command、Hook、SDK、SendService 运行。模型和最终平台驱动为合成实现，不能据此声明真实模型或 QQ 验收。Host 与 SDK 文件逐一匹配所选验收基线的来源锁；没有私有 Host 补丁。日常 CI 只运行当前插件的功能测试。

`peers.json` 固定 Identity/Hush 的真实提交，避免任意远端分支变化使旧证据看似仍有效。更新对等插件后先运行它们的测试并提交，再执行：

```bash
python maibot-maivita-plugin/ecosystem/sync_from_workspace.py --workspace /path/to/super-maimai
```

该命令仅复制现有协调层白名单源码/测试及联合脚本，刷新对等提交，不复制配置、数据库、密钥或测试实例。修改加载、权限、生命周期或跨插件接口后，手动验证对应组合。若只改此目录中的协调源码，请先同步工作区交付副本，再制作最终包；必须检查 `coordinator-source.json` 与实际包一致。

本地可运行：`python tools/run_joint_native.py --host-source /path/to/pinned/Host --source-lock maibot-maihush-plugin/tests/fixtures/source-lock-1.3.15.json --output /path/to/new/evidence --auto-routes --trusted-management`。需先准备与来源锁一致的独立 Host／SDK 环境，输出目录必须全新；`--trusted-management` 测试官方命令入口。

联合验收与内部审查记录保存在工作区根目录 `docs/`，不作为插件源码分发。同步工具包含 README、UPDATE_NOTES、配置示例和必要测试；每次发布前校验两份协调层源码与摘要一致。
