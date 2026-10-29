# 当前源码的验收规则

MaiVita 0.6.1 的证据必须匹配当前源码和验证脚本的指纹。`default-install.json` 表示默认安装与隔离联合门禁，`integration-ready.json` 表示包含真实模型的完整 I1；二者不是同一结论。旧版 0.3.1 的真实模型通过记录不能沿用到本版。

本版自动验证包括 Windows / SDK 2.15.0、Linux / SDK 2.16.0 全量单元，原版 Runner，完整 Host 无模型启动/重载，以及四插件原生联合场景。新的生活管理确认、权限拒绝、并发与重载去重、公开关系过滤、人设变化取消发送、Planner typed items 往返、静音独立约束和离线备份恢复均有相应回归。

GitHub 的 MaiVita checks 运行单仓库回归；Super MaiMai joint contracts 固定 peers.json 对等提交并运行真实原版 Host 与四插件，模型和最终发送驱动为合成实现。最终结论以最新提交实际 Actions 结果与交付包内 JOINT_CLOSEOUT.md、bundle.json、evidence 为准。CI 不持有生产密钥，也不连接 QQ。

本轮没有新增付费模型请求，当前源码完整真实模型 I1 不能标为通过。真实 QQ、实际模型质量、生产部署与长期运行观察由人工验收记录。快速验收使用交付包 FAST_ACCEPTANCE.md，不要求先等七天。

H01/H02 和通用 H03 仍未实现并明确关闭，已从当前无补丁方案的交付依赖中排除。官方 Command 的有限生活管理已提供，不把只读策略返回值伪装成通用跨插件执行令牌。
