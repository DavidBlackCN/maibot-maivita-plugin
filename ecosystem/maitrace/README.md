# MaiTrace 固定适配

上游：[Rabbit-Jia-Er/MaiTrace](https://github.com/Rabbit-Jia-Er/MaiTrace)，版本 6.3.0，提交 `712c777aab869c68808f186c24dddf4cab3e36d1`。适配号 `vita-coordinate-1`；保留原插件 ID、版本号与许可证。`patches/0001-vita-coordinate-space.patch` 是全部源码差异，不维护另一套空间协议实现。

补丁增加只读 `get_space_contract` v1、一轮 `browse_once_api` v1，以及发布接口的可选 `life_basis`、`expected_account` 和结构化结果。生成后通过 Vita 公开 API 再检查睡眠、忙闲和活动依据。浏览共用互斥，最多处理 5 条允许读取的动态，每条最多检查 5 条白名单评论；自己的动态可读，但陌生评论者不会自动获得互动资格。整轮成功仅代表浏览流程结束，不保证每次赞评均成功。

`life_basis.current=true` 时，换活动使依据失效；已结束活动以公开历史重新校验。可选配图明确失败时降级文字；空间提交结果不明时返回 `unknown`，禁止二次发布。浏览开始后允许收尾，不在每次赞评前反复查询 Vita。

推荐配置见 `recommended.toml`。必须关闭自身生活、日程、私聊主动、原生空间定时和聊天素材采集。`monitor.read_list_type="whitelist"`，`monitor.read_list` 是提供方允许互动用户；Coordinate 的名单必须是其子集。空名单不互动。图片默认关闭，真实登录和 Cookie 仍使用上游配置流程。

重建（输出必须是新目录）：

```powershell
python build.py --source /path/to/MaiTrace-git --output /path/to/new-adapted-MaiTrace
```

交付包也带固定上游 ZIP 与 `pin.json`，可把 `--source` 改为该 ZIP，离线校验并重建。构建需 Git；补丁不匹配会停止。`ADAPTATION.json` 记录来源、补丁和源码哈希。

`tests/native_fixture.py` 只用于隔离 Host 验收，替换模型/平台边界。不得复制到实际运行的 MaiTrace，也不包含在适配安装包中。
