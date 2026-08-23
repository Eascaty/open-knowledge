# Pipeline app

Python 本地流水线是知识系统主写入方和唯一任务处理方，负责接收、去重、解析、分类、提炼、SQLite schema、Vault、静态数据生成与发布门禁。可选 Java CLI 只能按同一 schema 将本地文件安全入队，不能处理任务或迁移数据库。

日常使用在 macOS 上双击仓库根目录的 `打开知识库.command`，或运行 `scripts/knowledge-manager start`。本地知识管家以单实例后台进程监听收件箱，自动调用完整流水线，并持续提供上一版通过检查的网站；内部模块路径不作为用户入口。
