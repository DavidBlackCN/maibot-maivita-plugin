# MaiVita 0.5.2

本轮完整功能、命令、联动和升级说明统一见 [PRODUCT_GUIDE](PRODUCT_GUIDE.md)。

此前阶段文档中的“管理入口不可用”“需要私有 Host 补丁”“只支持醒来表达”不再描述本版；通用 H01/H02/H03 的未实现边界仍有效。最终验收以当前源码指纹及交付 bundle.json 对应报告为准。

本次修复：管理与登记使用官方 Host 会话路由中的 Bot 账号，兼容 SnowLuma 的空备用 QQ 配置；Identity 校验拒绝会通过普通发送返回提示；Hook 使用 Host 支持的 continue + modified_kwargs；Vita 计划文本标明已取消/替换。无 Host 补丁，无新增特权。
