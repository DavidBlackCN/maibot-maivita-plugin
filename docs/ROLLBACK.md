# 回滚与恢复

1. 停止请求，禁用 MaiVita 或停止 Host，等待模型、worker、coordinator 任务取消以及 writer_lock 与 Runtime 锁释放。不能手工删除运行中的锁文件。
2. 备份插件代码、配置、完整授予数据目录和 authority，包含 life/maivita.db、life/management.sqlite3、事件水位及确认账本。使用 [产品指南](PRODUCT_GUIDE.md) 中的 product_bundle.py 在停机并取得原写锁后制作一致快照。
3. 恢复到新的空目录，核对 checksum、Schema、状态及事件数量。停止 Host 后成套替换匹配的代码、配置和数据；当前数据先另存，不能将旧文件叠加到新 WAL 上，不重放已发送事件。
4. 当前入口只运行 life，并验证原 writer_lock 和 life authority。激活失败会释放自身资源，不改写为其他引擎；修正配置或恢复匹配的备份后再加载。

配置、数据库与 authority 不是跨文件事务，需成套备份和恢复。当前有效配置段为 plugin、instance、model、routine；源码回退时使用与目标代码匹配的备份配置。不要移动数据、重命名数据库或猜测性降级 Schema。已发送消息和供应商模型调用不可回滚。

tools/rehearse_ready.py 仅在合成隔离实例验证原生 life 组件、单写入锁和快照恢复。恢复文件位于临时目录，验证后删除，不覆盖源数据库，也不生成安装包。历史验收报告不代表当前源码或生产回滚已通过。
