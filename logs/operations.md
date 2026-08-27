# 操作记录

> 本文件记录对项目产生实质影响的操作，不记录密码、Cookie、Token 或完整私密内容。

## 2026-07-27

### 需求与架构梳理

- 确认系统底线：无付费 API、本地优先、可自动运行。
- 确认最终需要在线随时访问，而不是仅有本地文件。
- 确认专业信息架构是严格的母子级链路。
- 确认当前一级模块至少包括金融、AI、技术，但不限制未来增加其他模块。
- 确认 Java 开发需要继续展开 JVM、并发、Spring、数据库、微服务等子级。
- 确认账号接入与网页爬虫暂不进入第一阶段。

### 本机只读检查

- 检查 Mac 硬件：Apple Silicon M3、16GB 内存。
- 检查可用磁盘：约247GB。
- 检查基础命令与工具可用性。
- 验证本机 SQLite 支持 FTS5 trigram 中文检索。
- 未安装知识库运行依赖，未下载本地模型。

### 方案研究

- 调研本地 LLM Wiki、不可变原始资料、Markdown 编译知识库和 lint 工作流。
- 调研 Ollama、SQLite FTS5、Trafilatura、MarkItDown、OCRmyPDF、whisper.cpp。
- 调研 Cloudflare Pages 免费限制、静态访问、Access 保护和 GitHub Pages 限制。
- 为 Codex 添加了 `openaiDeveloperDocs` 官方文档 MCP 配置；后续会话可能需要重启后加载。

### 交互示意

- 创建第一版通用知识网络示意。
- 根据用户反馈，改为专业母子树。
- 融合金融、AI、技术三条主干和 Java 开发子树。

### 文档初始化

- 确认父目录 `$HOME/ai` 存在且为空。
- 按用户最终要求将唯一项目根目录确定为 `$HOME/ai/knowledge`。
- 创建总体设计活文档、工作流、分类配置、状态、决策和操作记录。
- 在项目外的系统依赖保持不变；实现阶段先使用项目内代码和现有 Python/SQLite。

## 2026-07-30

### 全链路实现

- 完成 Python 3.9 标准库项目、SQLite schema、FTS5、任务队列和 CLI。
- 完成文件接收、SHA-256 去重、不可变 raw、Markdown、逐级分类和 Vault。
- 完成原生静态网站、中文搜索、专业树、证据区、知识图、响应式和 PWA。
- 完成数据库、磁盘、隐私、断链、构建清单、备份与发布门禁。
- 增加 `scripts/run-pipeline` 单命令离线闭环和 launchd 项目内模板。
- 未安装任何系统依赖，未修改项目外的持久用户目录，未执行网络发布。

### Code review 修复

- 修复 private 候选包可能通过 public 发布门禁的问题。
- 修复分类节点退休后产生孤立文档引用的问题。
- 拒绝 symlink 输入并约束 raw 读取只能位于 `data/raw/`。
- 核心、网站、doctor、gate、backup 与完整自动化使用统一项目锁。
- 公开来源 URL 删除全部查询参数和 fragment。
- 静态资源改为短缓存验证；网站回滚目录使用随机唯一名称。
- 未知 SQLite schema 版本不再被静默覆盖。

### 验证

- Python 3.9 语法解析通过。
- 18 项 unittest 全部通过。
- zsh 脚本语法、launchd plist 与前端 JavaScript 语法通过。
- 三份样例完成9个任务；金融、AI、Java/G1 路径全部正确。
- 第二次运行0新增、3重复、0新任务。
- 本地健康检查与发布门禁通过；所有检查网络请求为0。

### 正式目录交付

- 将源码、配置、文档、测试、脚本、launchd 模板和静态站同步到 `$HOME/ai/knowledge`。
- 未复制临时工作区数据库或临时备份；在正式目录重新生成干净 SQLite。
- 在正式目录执行 `scripts/run-pipeline`：0失败、发布门禁通过、健康状态 PASS、网络请求0。
- 在正式目录重新执行18项测试：全部通过。
- 正式目录健康检查：SQLite integrity PASS、外键0异常、隐私0异常、断链0。
- 在 `exports/private/backups/` 生成并验证初始 SQLite 一致性快照。
- 使用本地浏览器完成桌面与手机响应式验收；技术分类导航正常，PWA资源全部返回成功，控制台0错误。
- 在正式项目内初始化本地 Git；基线提交为 `a9b0c5a`，未配置远程仓库、未上传内容。

### 首份真实资料演示（2026-07-30 00:54:24 +0800）

- 确认桌面最新项目是 macOS Alias `2025.10.20.md的替身`，实际内容文件为 `$HOME/Desktop/2025.10.20.md`。
- 只读检查确认其为 Java 综合学习笔记；将副本放入 `inbox/files/`，未移动或修改桌面原文件。
- 执行 `scripts/run-pipeline`：导入1份、完成3/3个任务、0重试、0失败、网络请求0。
- 生成1份知识文档；网站构建、private 发布门禁与本地健康检查全部通过。
- 搜索“泛型”和“hashCode”均可命中。规则分类当前落在 `技术 / 程序员 / Java开发 / JVM / 垃圾回收 / G1`。
- 识别到综合长文档的分类粒度问题：后续应按 Markdown 标题切分为多张知识卡片，再分别挂入 Java基础、JVM、并发等节点；本次不改写用户原文。

### GitHub 开源准备（2026-08-09 18:06:27 +0800）

- 用户确认公开仓库目标为 `Eascaty/personal-knowledge-os`，许可证为 Apache-2.0，并保留 Python MVP、增加 Java 模块。
- 审计本地 Git：无远程仓库；真实知识、SQLite 数据库、私密导出和生成站点均未被跟踪。
- 运行现有测试套件，18/18 通过。
- 从 GitHub CLI 官方 Release 下载并按官方 SHA-256 校验 `gh 2.97.0`；通过本机 v2rayN SOCKS 代理完成 `Eascaty` 登录。
- 增加许可证、贡献指南、安全策略、Issue/PR 模板、Dependabot、GitHub Actions CI 与 Java 增量演进路线。
- 本阶段尚未创建远程仓库或上传代码，等待最终本地验证。

### GitHub Actions 首次 CI 修复（2026-08-09）

- 已创建私有安全暂存仓库 `Eascaty/personal-knowledge-os`，并推送开源候选分支、创建草稿 PR #1。
- 首次 GitHub Actions 在 Python 3.9、3.12、3.13 三个任务中均于测试入口退出，错误为 `./scripts/test: cannot execute: required file not found`。
- 根因是测试脚本使用 macOS 自带、Ubuntu Runner 默认不存在的 `/bin/zsh`，并写死系统 Python 路径。
- 经用户确认，将测试入口改为 POSIX `sh`，使用可移植的项目路径解析和 Actions 已配置的 `python3`。

### GitHub 开源首发完成（2026-08-09）

- CI 修复提交 `2ffc6de` 已推送，GitHub Actions 的 Python 3.9、3.12、3.13 三个任务全部通过。
- PR #1 已使用普通合并进入 `main`，保留许可证、文档、协作配置、CI 修复等全部中文分步提交。
- 仓库 `Eascaty/personal-knowledge-os` 已由私有安全暂存切换为 Public。
- 公开前后检查确认真实知识、SQLite 数据库、私密导出和生成站点均未进入 Git 跟踪范围。

### Java J1 领域模型与只读 API（2026-08-09 19:23 +0800）

- 从 GitHub Issue #5 建立 `agent/java-j1-readonly-api` 功能分支，采用 Java 21、Spring Boot 3.5.3、Maven 和 SQLite JDBC。
- 增加知识条目、分类节点、脱敏来源和关系边领域模型，以及带 `api_version=v1` 的健康、分类树、列表、详情和搜索接口。
- SQLite 适配器采用只读连接；知识查询强制过滤 `public`，API 来源结构不包含 `origin`、`raw_path` 或绝对路径。
- 使用官方 SHA-256/SHA-512 校验临时 Temurin JDK 21.0.12 与 Maven 3.9.11，未替换本机 Java 8 或修改系统级配置。
- Java 7 项测试全部通过，覆盖 canonical JSON 契约、MockMvc、SQLite 集成、private 过滤、路径脱敏和 SQL 通配符转义。
- Maven `verify`、JaCoCo 报告和原有 Python 18 项测试全部通过；GitHub Actions 已增加独立 Java 21 任务。
- 使用打包后的 Spring Boot JAR 在 `127.0.0.1:18080` 完成真实 SQLite HTTP 冒烟：健康 `UP`、schema v1、分类根节点正确；因真实资料均为 private，公开列表为0。
- 冒烟后正常关闭临时服务；最终 doctor 为 PASS，隐私问题0、密钥匹配0、断链0。
- 代码审查后将 Spring Boot 默认监听地址固定为 `127.0.0.1`，并为 Maven 增加每月 Dependabot 检查。

### Java J1 GitHub 发布完成（2026-08-09）

- 将6条中文分步提交推送至 `agent/java-j1-readonly-api`，创建 PR #6 并通过 `Closes #5` 关联 J1 Issue。
- PR 与合并后的 `main` 均通过 Java 21、Python 3.9、3.12、3.13 四项 GitHub Actions 检查。
- PR #6 已使用普通合并进入 `main`，保留服务骨架、API、测试、CI、安全和文档提交；Issue #5 自动关闭。
- 发布 `v0.3.0：Java J1 只读知识 API`，并创建 J2 中文全文搜索与性能基准 Issue #10。

### 发布后缺口审计与 v0.3.1 加固（2026-08-09）

- 审计仓库设置、开放 PR/Issue、Release、社区健康、依赖图、代码扫描、版本、构建入口和 JaCoCo 报告。
- 确认 Git 历史与公开归档未包含真实知识、SQLite、私密导出或生成站点；依赖安全告警为0。
- 为 `main` 启用 PR、四项必需 CI、管理员保护、禁止强推/删除和会话解决要求；仅保留普通合并并启用合并后删分支。
- 启用 Dependabot 安全更新、Secret Scanning、Push Protection、私密漏洞报告和 CodeQL 默认扫描。
- 增加9个仓库 topics、CODEOWNERS 和 Java J2 里程碑；Issue #10 已增加 `java`、`enhancement` 标签。
- 使用 Maven Central 官方 Wrapper 3.3.4 脚本和 Apache Maven 3.9.11；官方 SHA-512 校验通过，并固定分发 SHA-256。
- Maven Wrapper 首次下载验证成功；Java 7项与 Python 18项测试通过，JaCoCo 指令88.2%、分支67.5%。
- CI 升级 checkout v7、setup-python v7、setup-java v5、JaCoCo 0.8.15，增加并发取消、10分钟超时和覆盖率硬门槛。
- Dependabot 忽略 Spring Boot 跨主版本升级，项目版本统一为 `0.3.1`。

### v0.3.1 GitHub 发布完成（2026-08-09）

- PR #12 的 Java 21、Python 3.9/3.12/3.13 与 CodeQL Java/Python/JavaScript 检查全部通过，并使用普通合并进入受保护 `main`。
- CodeQL 三语言结果0条，Secret Scanning 0条，Dependabot 安全告警0条。
- 关闭已由 PR #12 取代的 Dependabot PR #2、#3、#7、#8；Spring Boot 4 PR #9 已按跨主版本忽略策略自动关闭。
- 从合并后的 `main` 使用 Maven Wrapper 构建 `personal-knowledge-service-0.3.1.jar`，Java 7项测试和覆盖率门禁通过。
- 发布 `v0.3.1：可复现构建与仓库安全加固`，附带正式 JAR 与 SHA-256 `edc3e9d4775ca01a66a8c9af6d47335164843fb119b1d3a46bb5d9f081bebc25`。

### README 使用指南与效果图优化（2026-08-09 22:57 +0800）

- 将 README 从工程索引重构为面向用户的项目首页，明确项目用途、适用人群、输入输出、三分钟体验和日常使用流程。
- 补充数据保存位置、默认私密策略、公开构建边界、Java 可选服务、常见问题与跨平台现状，避免首次使用者误解网络和隐私行为。
- 使用用户明确同意公开的本地知识地图截图作为 README 效果图；资源保存为 `docs/assets/knowledge-map.png`，未加入正文、凭据或本地绝对路径。
- README 的 Spring Boot 版本标识由3.5.3同步为当前 main 使用的3.5.16。
- 复验 Python 18项测试全部通过；doctor 状态 PASS，隐私问题0、密钥匹配0、断链0、网络请求0。

### GitHub Pages 虚构公开 Demo（2026-08-10）

- 根据用户澄清，仓库保持为独立开源工程，不嵌入维护者身份或外部材料；同步移除工程文档中与项目实现无关的展示性措辞。
- 新增隔离 Demo 构建器，只读取 `tests/fixtures/` 中3份固定虚构资料，在临时目录创建独立 SQLite 并生成 public 站点。
- 新增 `scripts/build-demo`、Public Demo CI 和 GitHub Pages 工作流；部署产物必须经过数据库、构建清单、public 可见性、隐私、密钥与断链门禁。
- 本地 Demo 构建完成：3篇文档、9项任务、0失败、门禁 PASS、网络请求0；Python 测试增加为19项并全部通过。
- PR #16 的 Java、Python、Public Demo 与 CodeQL 共9项检查全部通过，并使用普通合并进入受保护 `main`。
- 为仓库启用免费的 GitHub Pages Actions 发布源，并将 Public Demo 增加为 `main` 第5项必需检查。
- 首次部署成功：`https://eascaty.github.io/personal-knowledge-os/` 返回 HTTPS 200；线上搜索、详情、关系地图、桌面和手机布局正常，控制台0错误。
- 使用线上虚构公开 Demo 生成干净效果图 `docs/assets/pages-demo.jpg`，README 不再使用带系统全屏提示的旧截图。

### v0.4.0 结构重构启动（2026-08-10 02:34:00 +0800）

- 等待 GitHub Pages 任务完成并独立确认 `main` 的 CI、Pages 和 CodeQL 全部成功。
- 清理9条已合并本地 `agent/*` 分支、4条 GitHub 历史分支及失效远端引用；提交历史仍由 `main` 合并记录和 tag 保留。
- 从提交 `677023e` 建立唯一重构分支 `codex/architecture-restructure`。
- 重构期间保持 SQLite schema v1、原始资料、知识 ID、分类 ID、公开门禁和线上 Demo 行为不变。

### 私密工作区与 SQLite 路径迁移（2026-08-10 04:16:04 +0800）

- 将本机私密运行目录从根级 `inbox/`、`data/`、`vault/`、`site/` 和 `exports/private/` 迁移到统一的 `workspace/`，公开候选 `exports/public/` 保持不变。
- 迁移前创建 SQLite 一致性备份 `workspace/exports/private/backups/knowledge-20260810-033357-f9f2ba1d54b9.sqlite3`，SHA-256 为 `f9f2ba1d54b92000f35f3c93f145d84397229c6793f352c3f5638027a7c24b0b`。
- 在单一事务中更新数据库内的原始资料与标准化文档路径；`PRAGMA integrity_check` 返回 `ok`，现有1份资料和25个分类节点均可读取。
- 迁移后完整运行 `scripts/run-pipeline`：既有资料正确判定为重复，未新增任务；站点生成、发布门禁和健康检查均通过，网络请求为0。

### v0.4.0 重构分支与草稿 PR（2026-08-10 05:39:59 +0800）

- 将五阶段重构分支 `codex/architecture-restructure` 推送到 `Eascaty/personal-knowledge-os`，工作区提交范围干净且未包含真实资料、SQLite、私密构建或本机运行配置。
- 创建草稿 PR #18 `架构：完成 v0.4.0 五阶段工程重构`，目标为受保护的 `main`。
- 首轮远端验证全部通过：Python 3.9/3.12/3.13、Java 21、Public Demo、CodeQL Java/Python/JavaScript 均成功。

### v0.4.0 合并与线上验收（2026-08-11 21:26:30 +0800）

- 最终审阅发现并清理19个拆分模块的文件末尾多余空行；新增架构门禁首次运行又清理3个历史入口文件，并持续阻止 Python 源码行尾空白和不稳定的文件末尾换行。
- PR #18 转为 Ready 后，全部必需检查通过并以普通合并进入受保护的 `main`；合并提交为 `b1f60f93783325749725ffe4638b263f0a72f299`。
- `codex/architecture-restructure` 已从本地与远端删除，本地 `main` 已快进同步至远端。
- 合并后的 Python、Java、Public Demo、CodeQL 与 GitHub Pages 工作流全部成功；公开站点继续返回 HTTPS 200。
- 实际浏览器复验首页、`G1` 搜索、知识详情、关系地图和390×844移动端布局均正常，页面控制台0错误。
- 关闭 Dependabot PR #19 并删除其远端分支：该变更为 `actions/configure-pages` 跨主版本自动升级，不符合“升级前先运行固定样本测试”的项目规则；现网继续使用已验证版本。

### Java J2 中文全文搜索交付候选（2026-08-12 19:42:22 +0800）

- 从 Issue #10 建立唯一功能分支 `codex/j2-fts-search` 和草稿 PR #21，`main` 未被直接修改。
- Java API 复用现有 SQLite FTS5 trigram 索引，通过 BM25 排序；短查询、特殊语法和无 FTS 命中安全降级为参数化 LIKE。
- 公开搜索结果增加纯文本高亮标题、命中片段和相关度；FTS 与 LIKE 查询均强制过滤 private 内容，未扩大 Java 只读边界。
- 固定2,000条虚构 public 中文资料完成10次预热和50次测量，中位数1.325ms、p95 2.260ms；测试不读取真实个人知识。
- PR #21 首轮 Python 3.9/3.12/3.13、Java 21、Public Demo 与 CodeQL 三语言检查全部成功。
- PR #21 已以普通合并进入 `main`，合并提交为 `08f8eaed088be48fe9ce762daeda6aef7a6c6180`；Issue #10 自动关闭，本地与远端 J2 分支均已删除。
- 合并后的 `main` 再次通过 CI、CodeQL 三语言和 GitHub Pages 部署；线上公开 Demo 返回 HTTPS 200。

### Java J3 受限离线导入候选（2026-08-12）

- 创建 Issue #22，范围限定为本地文件入队、不可变 raw、内容哈希幂等、事务回滚和跨 Python/Java 的项目锁；不增加 HTTP 写接口。
- Java CLI 只复用 schema v1 创建 source、初始 extract 任务和审计事件；Python 继续独占 schema、任务处理、分类、提炼与发布。
- Python 项目锁切换为 POSIX 记录锁，并保留同进程竞争保护，使 Java `FileChannel` 与 Python 写任务共享同一个内核互斥边界。
- 草稿 PR #23 的 Python 3.9/3.12/3.13、Java 21、Public Demo 与 CodeQL Java/Python/JavaScript 门禁全部成功，已具备合入受保护 `main` 的条件。

### Java J4 可部署只读服务候选（2026-08-12）

- 创建 Issue #24 和草稿 PR #26；Docker 只作为可选 Java 只读 API 部署层，不替代 Python 流水线或静态网站。
- Linux 容器门禁发现并修复 SQLite JDBC 原生库加载目录与 WAL 快照只读打开问题；最终以非 root、只读根文件系统、只读数据库和 `mode=ro&immutable=1` 通过冒烟。
- PR #26 的 Python 3.9/3.12/3.13、Java 21、Container Smoke、Public Demo 与 CodeQL Java/Python/JavaScript 全部成功。

### v0.4.0 可验证发布候选（2026-08-23 17:46 +0800）

- 创建 Issue #27 和 PR #28；正式软件版本只允许从受保护 `main` 上版本一致的语义版本 tag 生成。
- PR #28 的 Java 21 双构建哈希、Container Smoke、Python 3.9/3.12/3.13、Public Demo 与 CodeQL Java/Python/JavaScript 全部成功。
- 在保留原有五项必需检查的基础上，将已稳定通过的 `Container Smoke` 加入 `main` 分支保护；严格同步分支规则保持启用。
- 发布工作流将生成版本化 JAR、SHA-256 和 GitHub build provenance，且不读取 `workspace/`、SQLite、原始资料或私密站点。

### v0.4.0 正式发布与知识管家合并（2026-08-23）

- 17:52:17 +0800，`v0.4.0` 正式 Release 发布，目标提交为 `3c2dd81a1bb579e12cd0b707433b6fe046f399ac`。
- 正式产物 `personal-knowledge-service-0.4.0.jar` 下载后通过 SHA-256 校验，摘要为 `4d463146880442f9cfd2647d4ae543f41a56f075d9f0533d095426fd1830c22c`；GitHub build provenance attestation 验证通过。
- 18:24:56 +0800，PR #30 以普通合并进入受保护 `main`，合并提交为 `830efd928598658bfed3a887672fa9e7d047148c`。
- 合并后的 CI、GitHub Pages 与 CodeQL 均成功；本记录不包含任何真实知识、SQLite 路径或私密构建信息。
