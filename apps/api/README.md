# API app

Java 21 + Spring Boot API 默认只读 `workspace/data/state/knowledge.sqlite3` 的 SQLite schema v1 或 v2，并只返回允许暴露的知识字段。

HTTP 服务不拥有导入、分类、迁移或任务状态写入权。控制器只依赖应用服务，应用服务再访问只读仓储；接口以 `packages/contracts/openapi.yaml` 为准。

`GET /api/v1/search?q=并发编程` 优先使用现有 SQLite FTS5 trigram 索引，并通过 BM25 排序；短查询、特殊语法或无 FTS 命中时安全降级为参数化 LIKE。结果使用 `[[...]]` 标出命中词，片段已做 HTML 转义，查询始终包含 `visibility='public'` 过滤。

在仓库根目录使用 `./scripts/java-test` 运行全部测试与覆盖率门禁，使用 `./scripts/java-service` 启动本地服务。

J4 提供 `apps/api/Dockerfile` 与根目录 `compose.yaml`。镜像采用固定 Temurin 21 补丁版本、多阶段构建和非 root UID 10001；Compose 只读挂载已验证 SQLite 快照，API 以 `mode=ro&immutable=1` 打开且不创建 WAL/SHM sidecar。默认绑定 `127.0.0.1`，启用只读根文件系统并移除全部 Linux capabilities。`/actuator/health/liveness` 只判断进程存活，`/actuator/health/readiness` 额外要求数据库可读且 schema 为 v1 或 v2；除 health 外不暴露其他 Actuator 端点。

J3 提供独立的离线文件导入 CLI：`./scripts/java-import <文件>`。它兼容 SQLite schema v1 和 v2，只写入不可变 raw、source、初始 extract 任务和审计事件，并拒绝其他 schema 版本；它不开放 HTTP 写接口，也不执行分类、提炼或 schema 迁移。Java 与 Python 写入共用 `workspace/data/state/knowledge-os.lock`，有其他写任务时立即拒绝运行。
