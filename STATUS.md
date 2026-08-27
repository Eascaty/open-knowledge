# 项目状态

- 更新时间：2026-08-23（Asia/Shanghai）
- 阶段：v0.4.0 本地知识管家、网页投放中心与安全候选站发布均完成交付验证
- 总体状态：用户可在本机知识网站直接拖放资料并观察全链路进度；逐文件文档关联、刷新恢复、失败重试、旧站保护和安全边界已通过真实端到端测试，Docker 不参与日常知识库流程

## 已实现

- 系统采用严格的专业母子树，并允许无限增加一级模块和子级。
- 当前已知主干：
  - `金融 / 财经 / 信用卡 / 美股`
  - `AI / Agent / 智能体`
  - `技术 / 程序员 / Java开发`
- Java 开发允许继续展开 JVM、并发、Spring、数据库、微服务等专业子树。
- 核心处理在 Mac 本地完成，不使用付费模型 API。
- 最终知识库需要在线随时访问，Mac 关机后线上现有版本仍可打开。
- 推荐使用 Cloudflare Pages 静态托管和 Cloudflare Access 私密登录。
- 账号聊天接入与批量网页爬虫暂缓。
- Python 3.9 标准库 CLI 与 SQLite schema。
- 文件扫描、SHA-256 去重、不可变 raw、Markdown 标准化。
- 文本、Markdown、代码、HTML、DOCX；PDF 可选调用本地 `pdftotext`。
- 规则提炼器默认零依赖运行；可选本地 Ollama。
- 三阶段可重试队列、失败隔离、FTS5 中文搜索。
- 严格逐级母子分类、Vault、canonical JSON、搜索和关系图数据。
- 原生 HTML/CSS/JS 静态网站、响应式、PWA、private/public 隔离。
- 项目锁、SQLite 一致性备份、隐私、断链、数据库和发布门禁。
- `scripts/run-pipeline` 单命令离线全链路。
- `打开知识库.command` 与 `scripts/knowledge-manager` 提供项目内双击/单命令启动、后台单实例、自动打开浏览器和安全停止。
- 本地知识管家检测收件箱稳定变化后自动运行全流水线；首次构建显示自动刷新等待页，失败时继续提供上一版正常网站。
- 本机知识网站提供“投放资料”中心，支持拖放、多选、两路并发上传、逐文件进度、刷新恢复、重复资料识别和完成后自动打开知识；公开静态 Demo 自动隐藏入口。
- 浏览器上传使用独立内存会话、严格 Host/Origin 校验、64 MiB 上限、私密未监控暂存和原子 inbox 发布；控制面、构建版本和 private 数据不会进入 Service Worker 缓存。
- 全流水线先构建候选站，只有任务无失败/待重试且 Gate、Health 均通过时才原子替换正式站；发布异常自动恢复上一版。
- 控制接口仅绑定 `127.0.0.1`，令牌与完整异常留在 Git 忽略的私密状态/日志中，网页状态不返回绝对路径或错误原文。
- Java 21 + Spring Boot 3.5 领域模型、SQLite 只读仓储与 API v1。
- 健康检查、分类树、公开知识列表/详情/搜索接口。
- Java 搜索使用 SQLite FTS5 trigram 与 BM25 排序，短查询和特殊语法自动降级为参数化 LIKE；结果携带纯文本高亮片段且始终过滤 private 内容。
- Java J3 提供受限离线文件导入 CLI：内容哈希幂等、不可变 raw、单事务入队，与 Python 共用跨运行时项目锁；HTTP API 仍保持只读。
- Java API 结构性排除 private 内容、`origin`、`raw_path` 和绝对路径。
- Java HTTP 默认仅监听 `127.0.0.1`，外部监听必须显式设置环境变量。
- Java API 提供 Actuator liveness/readiness；readiness 复用 schema v1 只读检查，不公开健康详情。
- J4 容器使用固定 Temurin 21 补丁版本和非 root UID 10001；Compose 根文件系统与数据库均只读，移除全部 capabilities，并默认只绑定本机回环地址。
- Release 只接受已合入 `main` 且与 Python、Java、运行时和 CHANGELOG 一致的版本 tag；正式 JAR 同时生成 SHA-256 与 GitHub build provenance。
- launchd 项目内模板，未修改 macOS 系统目录。

## 已验证环境

- 电脑：Apple Silicon M3
- 内存：16GB
- 可用磁盘：约247GB（验证时）
- SQLite：支持 FTS5 与 trigram
- 推荐本地模型规模：4B–8B 量化模型，单并发

## 最近验证

- Python 3.9 语法解析通过。
- 56 项 Python unittest 全部通过。
- Web 读取适配与网页投放测试9/9通过；新增脚本已接入本地与 CI 的统一 Web 测试入口。
- 新增4项知识管家测试，覆盖收件箱指纹、首次等待页、成功更新、失败回退、状态脱敏、后台单实例复用与安全停止。
- 新增网页投放安全与真实端到端测试，覆盖半文件隔离、路径/类型/大小、0700/0600权限、DNS rebinding、Origin/令牌/CSP、重启恢复、上传到对应 `document.id` 以及令牌不进入站点。
- 新增候选站门禁和回滚测试；失败、待重试、健康检查失败与发布替换异常均不会覆盖上一版正常网站。
- DOCX 提取增加解压大小与压缩比限制，正常文档和压缩炸弹回归均通过。
- Python 存储、知识处理、检查和网站构建已拆为职责模块；兼容 facade 保留旧导入，架构测试限制实现模块不超过600行。
- 工程已迁移为 `apps/ + packages/ + workspace/` 单仓库：Python、Java、Web 各自归位，私密输入、SQLite、Vault 与私密构建统一进入 Git 忽略的 `workspace/`。
- 真实 SQLite 迁移前已完成一致性备份，迁移后完整性检查、现有资料去重、全流水线、发布门禁与健康检查通过。
- canonical JSON Schema v1 与只读 API OpenAPI v1 已集中到 `packages/contracts/`；Python 建站强制校验，Python/Java 共用虚构契约样例。
- Web 已通过数据源接口解耦静态包与 API v1；Java 控制器通过应用服务访问仓储，未来 App 无需读取 SQLite 或依赖页面内部状态。
- Web 静态/API 两种适配器场景2/2通过；Java J3 27/27通过，JaCoCo 指令89.4%、分支74.1%，门禁通过。
- J4 增加健康指标、实际 HTTP 探针与不可变 WAL 快照测试，Java 测试总数为34项；Linux 容器冒烟与覆盖率门禁均通过。
- Java J2 固定2,000条中文资料基准完成：50次测量中位数1.325ms、p95 2.260ms；1秒回归门禁通过。
- Python 包、Java JAR 与运行时版本统一为0.4.0，并由架构测试阻止版本漂移。
- `scripts/verify-release` 已验证正确 `v0.4.0`，并拒绝缺少 `v` 前缀与跨模块不一致版本；Release 工作流不读取任何私密工作区。
- PR #28 的 Java 21 双构建哈希、Container Smoke、Python 3.9/3.12/3.13、Public Demo 与 CodeQL 三语言检查全部成功；Container Smoke 已加入 `main` 必需检查。
- 重构后的公开 Demo 在实际浏览器完成搜索、详情、关系地图和390×844手机布局验收，控制台0错误。
- PR #18 经最终审阅转为 Ready，并以普通合并进入受保护的 `main`；重构分支已从本地和远端删除。
- 合并后的 Python 3.9/3.12/3.13、Java 21、Public Demo、CodeQL 三语言和 GitHub Pages 部署全部成功。
- 线上公开 Demo 再次完成首页、搜索、详情、关系地图和390×844移动端验收，页面控制台0错误。
- 三份样例生成9个任务，全部完成。
- 重复运行不新增资料或任务。
- 金融、AI、Java/G1 三条路径全部正确。
- 发布门禁会拒绝 private 候选包冒充 public。
- 分类节点删除后资料进入“待归类”，不会生成孤立引用。
- symlink 和越界 raw 路径回归测试通过。
- SQLite、隐私、断链、PWA 缓存和公开过滤检查通过。
- 正式项目目录已完成干净初始化、18项复验与一致性初始备份。
- 桌面端和手机端浏览器验收通过，页面交互正常且控制台0错误。
- 已完成首份真实资料 `2025.10.20.md` 的复制导入演示：原桌面文件未修改，1份资料完成3/3个流水线任务，搜索、网站构建、发布门禁与健康检查均通过。
- GitHub CLI 2.97.0 已从官方 Release 安装并通过 v2rayN 本地代理登录为 `Eascaty`。
- 已选择 `Eascaty/personal-knowledge-os`、Public 与 Apache-2.0，并完成许可证、贡献指南、安全策略、Issue/PR 模板、Dependabot 和 CI 的本地准备。
- 已建立 Java 21 + Spring Boot 增量演进路线；现有 Python MVP 保持可运行，不做无价值重写。
- 已创建私有安全暂存仓库和草稿 PR #1；首次 CI 暴露测试入口依赖 macOS `zsh`，已改为跨平台 POSIX `sh`。
- PR #1 已保留全部分步提交并合并；仓库 `Eascaty/personal-knowledge-os` 已切换为 Public。
- Java J1 使用 Temurin JDK 21.0.12 与 Maven 3.9.11 完成构建；7 项 Java 测试全部通过。
- Java 与 Python 联合复验通过：Java 7/7、Python 18/18，JaCoCo 报告已生成。
- Spring Boot 本机 HTTP 冒烟通过：schema v1 状态 `UP`，分类树正确，private 资料未进入公开列表。
- 最终健康检查 PASS：隐私问题0、密钥匹配0、断链0。
- Java J1 PR #6 已保留全部分步提交并合并，关联 Issue #5 已自动关闭。
- `v0.3.0` Java J1 版本已发布；J2 中文全文搜索已建立为 Issue #10。
- Maven Wrapper 3.9.11 已固定下载地址与 SHA-256，克隆后无需预装 Maven。
- Java 覆盖率为指令88.2%、分支67.5%，CI 门槛分别为80%和60%。
- `main` 已要求 PR、Java/Python四项 CI 与 Public Demo 构建，禁止强推/删除；管理员同样受保护。
- 已启用 CodeQL、Dependabot 安全更新、Secret Scanning、Push Protection 和私密漏洞报告。
- 仓库 topics、CODEOWNERS、跨平台行尾策略和 J2 里程碑已补齐。
- PR #12 已保留4条中文分步提交并合并；`v0.3.1` 已附带正式 JAR 与 SHA-256 发布。
- 最终 main CI 4/4 通过；CodeQL 三语言、Secret Scanning 和 Dependabot 安全告警均为0。
- README 已重构为面向首次使用者的中文项目首页，补齐项目用途、适用人群、三分钟体验、日常流程、数据目录、隐私边界、Java API、FAQ 与路线图。
- README 已加入经过用户确认可公开使用的本地知识地图效果图，并同步 Spring Boot 版本标识至3.5.16。
- 公开 Demo 构建器只读取3份固定虚构资料，在临时目录生成独立 SQLite 和 public 站点，不访问本机真实知识库。
- Python 测试增加为19项；新增 Public Demo CI 和 GitHub Pages 自动部署工作流。
- PR #16 已保留3条中文分步提交并合并；GitHub Pages 公开 Demo 已上线：`https://eascaty.github.io/personal-knowledge-os/`。
- 线上桌面与手机浏览器验收通过：分类、搜索、知识详情和关系地图正常，控制台0错误；HTTPS 响应200。

## 后续里程碑

1. 用户准备发布真实私密站点时，再配置 Cloudflare Pages + Access；默认仍不上传 private 数据。
2. 确认 Java API 的真实远程部署与访问者范围后，再设计认证、TLS 终止和同步；默认容器仅用于本机或受保护网络。
3. 确认原生 App 的目标平台与离线/同步需求后，再创建移动端应用；现阶段直接复用 OpenAPI v1。
