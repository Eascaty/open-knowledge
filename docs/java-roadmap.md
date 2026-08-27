# Java 演进路线

现有 Python 标准库实现是经过端到端验证的本地优先 MVP。Java 演进采用增量替换，不推倒已工作的流水线，也不制造无业务价值的模块。

## 目标技术栈

- Java 21
- Spring Boot 3
- Maven
- Spring Web 与 Bean Validation
- SQLite/PostgreSQL 可替换持久层
- SQLite FTS5；数据规模或分词需求超过单机 SQLite 边界后再评估 Lucene
- JUnit 5、Testcontainers、JaCoCo
- OpenAPI、Docker、GitHub Actions

## 里程碑

### J1：领域模型与只读 API

- 状态：已完成（2026-08-09）。
- 已建立 `apps/api/` Maven 模块。
- 已映射知识条目、分类节点、脱敏来源和关系边。
- 已提供健康检查、分类树、知识条目和搜索只读 API。
- API 只返回 `public` 条目，不返回 `origin`、`raw_path` 或本机绝对路径。
- 已使用契约测试证明 Java API 的核心字段与现有 canonical JSON 一致。
- 已将 `packages/contracts/openapi.yaml` 和 canonical JSON Schema 固化为跨应用 v1 契约，Java 与 Python 共用同一份虚构样例。
- 已通过 JDK 21、JUnit 5、SQLite 集成测试、MockMvc 和 JaCoCo 验证。

### J2：搜索服务

- 状态：已完成（2026-08-12），PR #21 已合入 `main`，Issue #10 已关闭。
- 已将独立搜索接口升级为 SQLite FTS5 trigram，并使用 BM25 对标题、标签、分类路径、摘要和正文加权排序。
- 低于3个字符的查询、包含 FTS 语法字符的查询及无 FTS 命中的查询自动降级为参数化 LIKE。
- 搜索结果提供 `[[...]]` 纯文本高亮标记、分页、稳定排序和可见命中片段；数据库查询始终过滤 private 内容。
- 查询上限固定为200字符，通配符按普通字符处理，返回片段进行 HTML 转义。
- 查询、分页、排序、高亮、隐私、特殊字符和降级路径均有集成测试。
- 固定2,000条中文资料的本机基准见 [`benchmarks/java-search.md`](benchmarks/java-search.md)。

### J3：导入与幂等

- 状态：受限实现与远端交付门禁均已完成（2026-08-12）。
- 增加独立 Java 文件导入 CLI，兼容 schema v1/v2，仅写入不可变 raw、source、初始 extract 任务与审计事件；HTTP API 仍无写端点。
- Python 继续独占 schema 迁移、任务处理、分类、提炼和发布；Java 不建表、不迁移、不处理任务。
- Python 与 Java 使用同一个 POSIX 项目锁，避免跨运行时并发写；每份文件的 raw 与 SQLite 变更构成一个失败可清理的事务边界。
- 已覆盖内容哈希幂等、缺失 raw 修复、symlink/大小/越界拒绝、raw 篡改、任务失败回滚和锁竞争。

### J4：可部署服务

- 状态：最小上线切片与远端 Linux 容器门禁均已完成（2026-08-12）。
- 提供固定 Temurin 21、多阶段、非 root 的 Docker 镜像和只读 Docker Compose 运行边界。
- 提供 Actuator 存活与数据库就绪探针；除 health 外不暴露管理端点。
- CI 使用 Python 当前初始化流程创建真实 schema v2 数据库，并验证容器用户、只读数据库与三个健康入口；Java 仍保留 schema v1 兼容测试。
- 保留完全本地、无账号、无付费 API 的默认运行模式；远程认证和同步待真实需求明确。

## 工程验收标准

每个里程碑应具备 Issue、Pull Request、自动测试和 Release 记录，并能解释架构取舍、真实问题与性能数据。
