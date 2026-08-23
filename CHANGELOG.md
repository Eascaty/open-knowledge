# 版本记录

## Unreleased

- 增加纯 Python 本地知识管家：单实例后台监听收件箱、自动运行全流水线并持续提供知识网站。
- 增加 macOS `打开知识库.command` 双击入口、首次构建等待页、状态/停止命令和失败保留旧站策略。
- 本地控制仅绑定回环地址，控制令牌和完整错误保存在 Git 忽略的私密工作区；网页状态使用脱敏字段白名单，静态服务拒绝符号链接越界读取。

## 0.4.0 — 2026-08-12

- 将 Python、Java、Web 重整为 `apps/`，共享契约集中到 `packages/contracts/`，真实私密运行状态统一到 Git 忽略的 `workspace/`。
- 按存储、处理、运维检查和网站构建职责拆分 Python 大模块，并保留旧导入兼容 facade。
- 增加 canonical JSON Schema v1、OpenAPI v1、标准库契约校验和 Python/Java 共用虚构样例。
- Web 增加静态包与 API v1 数据源适配器；Java 增加控制器与只读仓储之间的应用服务层。
- CI 增加 Web 适配器测试；覆盖 Python 27项、Java 26项、Web 2场景、Demo、健康门禁和桌面/手机浏览器验收。
- 精简分支与文档治理规则，本机运行配置不再进入 Git，保留可复制的 `runtime.example.json`。
- Java 搜索升级为 SQLite FTS5 trigram、BM25 排序、安全高亮和参数化 LIKE 降级，并增加固定中文数据性能基准。
- 增加受限 Java 离线文件导入 CLI、跨 Python/Java 的 POSIX 项目锁以及 raw/SQLite 回滚和幂等测试；HTTP API 保持只读。
- Python 包、Java JAR 与运行时版本统一为 `0.4.0`，自动测试阻止跨模块版本再次漂移。

## 0.3.1 — 2026-08-09

- 增加带 SHA-256 校验的 Maven Wrapper，不再要求预装 Maven。
- 增加 `.gitattributes` 与 CODEOWNERS，固定跨平台脚本行尾和代码所有权。
- CI 升级 Actions 与 JaCoCo，增加并发取消、超时和覆盖率硬门槛。
- Dependabot 保留 Spring Boot 3 主线，不再自动提交 4.x 跨大版本升级。
- Python 包与 Java JAR 统一为正式 `0.3.1` 版本。
- 启用 main 分支保护、Secret Scanning、Push Protection、Dependabot 安全更新、私密漏洞报告与 CodeQL。

## 0.3.0 — 2026-08-09

- 增加 Java 21 + Spring Boot 3.5 的只读知识服务。
- 增加健康检查、分类树、知识列表、详情与基础搜索 API v1。
- Java API 仅返回公开知识，并对来源绝对路径进行结构性裁剪。
- 增加 7 项 JUnit、SQLite 集成、MockMvc 和 canonical JSON 契约测试。
- GitHub Actions 增加 Java 21 Maven 构建与 JaCoCo 覆盖率报告。

## 0.2.0 — 2026-08-09

- 使用 Apache License 2.0 开源。
- 增加贡献指南、安全策略、Issue/PR 模板、Dependabot 和 GitHub Actions CI。
- 明确 Python MVP 与 Java 21/Spring Boot 增量演进路线。
- 公开仓库仍严格排除真实知识、数据库、私密导出和生成站点。
- 完成本地文件到 SQLite、Markdown、全文搜索、知识图和静态网站的闭环。
- 新增 `scripts/run-pipeline` 单命令自动化和项目锁。
- 新增 private/public 构建、PWA、安全响应头与 Cloudflare 发布适配。
- 新增数据库、磁盘、隐私、断链、构建清单和一致性备份检查。
- 修复 public 发布门禁、分类节点退休、symlink、raw 路径越界和并发竞态。
- 18 项自动测试与三资料端到端样例全部通过。

## 0.1.0 — 2026-07-27

- 建立 `$HOME/ai/knowledge` 单一工程根目录。
- 固化金融、AI、技术三条专业母子链路与 Java 子树。
- 采用 Python 标准库、SQLite、Markdown/JSON 和纯静态网站的零依赖核心方案。
- 默认本地运行、默认私密、默认不连接外网和不执行真实发布。
- 开始实现入库、去重、分类、搜索、知识树、知识图、网站、健康检查、备份和发布门禁。
