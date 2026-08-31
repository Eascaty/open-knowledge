# 个人知识体系总体设计

> 状态：活文档  
> 版本：0.5.0
> 更新时间：2026-08-31（Asia/Shanghai）
> 项目根目录：`$HOME/AI/knowledge`

## 1. 目标

建设一套本地优先、零付费 API、可自动运行并能在线随时访问的个人知识系统。

用户最终体验：

```text
投入本地文件
→ 系统自动解析与去重
→ 本地 AI 提炼知识
→ 逐级归入专业母子树
→ 更新搜索、关系图与报告
→ 自动构建私密网站
→ 在手机或任意电脑登录访问
```

网站已经发布的版本由云端静态托管，因此 Mac 关机后仍然可以打开；Mac 只负责处理新资料和发布新版本。

实现状态：本地入库、长文拆卡、分类、搜索、知识图、静态站、检查、备份恢复演练和发布适配代码已完成；项目内本地知识管家提供网页拖放投放、逐文件进度、收件箱监听、全流水线自动运行和持续网站服务；SQLite 写模型为 schema v2，Java 21 只读 API 兼容 v1/v2。
虚构公开资料已经通过 GitHub Pages 提供在线 Demo；真实私密知识的线上地址仍需用户以后登录 Cloudflare 并启用 Access。本地默认零网络请求。

## 2. 当前范围

### 第一阶段包含

- 本地文件投入
- Markdown、文本、代码、HTML、DOCX 等本地资料
- 文本型 PDF（本机存在免费 `pdftotext` 时）
- 原始资料保存
- 内容哈希与重复检测
- 本地模型摘要、分类和知识提炼
- 专业母子树
- Markdown 知识页
- 中文全文搜索
- 可点击知识地图
- 私密/公开静态网站
- 自动构建、检查与安全回滚
- 项目内单实例后台监听、首次等待页和双击启动入口
- 长 Markdown 二级标题拆卡、独立分类和可追溯原文行号
- 批量虚构验收、显式数据库迁移与非破坏性恢复演练
- 本机网页拖放投放、刷新后进度恢复和完成后定位知识

### 暂缓

- ChatGPT/Gemini 账号历史接入
- 手工网址下载与网页抓取
- 扫描 PDF OCR
- 浏览器实时聊天采集
- 批量网页爬虫
- RSS 全网订阅
- 大规模向量数据库
- 公开原始附件

暂缓项目以后通过新的输入适配器接入，不改变核心数据结构。

## 3. 信息架构

系统包含两种关系，必须分开。

### 3.1 专业母子树

这是稳定分类骨架：

```text
我的知识体系
├── 金融
│   └── 财经
│       └── 信用卡
│           └── 美股
├── AI
│   └── Agent
│       └── 智能体
├── 技术
│   └── 程序员
│       └── Java开发
│           ├── Java基础
│           ├── JVM
│           │   ├── 内存模型
│           │   ├── 类加载
│           │   ├── 垃圾回收
│           │   │   ├── G1
│           │   │   └── ZGC
│           │   └── 性能诊断
│           ├── 并发编程
│           ├── Spring生态
│           ├── 数据库
│           ├── 微服务
│           └── 工程实践
└── 未来新增一级模块
```

规则：

- 一级模块数量不设上限。
- 树深度不设固定上限。
- 每张知识卡只有一个主路径；一份原始来源可以拆成多张分别分类的卡。
- 一级至三级默认由用户定义并锁定。
- AI 只在允许层级以下增加子节点。
- 用户定义的专业链路优先于通用语义判断。

### 3.2 辅助知识关系

辅助关系不改变专业目录，包括：

- `supports`
- `contradicts`
- `extends`
- `derived_from`
- `mentions`
- `used_in_project`
- `supersedes`
- `related_to`

每条事实性关系必须指向证据段落、页码或来源片段。

## 4. 核心工作流

```text
INBOX
  ↓
RECEIVED
  ↓
DEDUPED
  ↓
EXTRACTED
  ↓
CARDS
  ↓
CLASSIFIED
  ↓
ENRICHED
  ↓
INDEXED
  ↓
BUILT
  ↓
PUBLISHED

任何阶段失败 → RETRY → QUARANTINE
```

### 4.1 常驻触发

- macOS 用户双击 `打开知识库.command`，或运行 `./scripts/knowledge-manager start`。
- 管理器只绑定 `127.0.0.1`，重复启动先核对实例标识并复用已有进程。
- 收件箱指纹包含相对路径、大小和修改时间；文件稳定3秒后才触发，避免复制到一半就读取。
- 首次尚未生成站点时返回自动刷新的等待页；成功后继续提供正式网站。
- 处理失败时保留上一版正常网站并按间隔重试，不用失败产物覆盖成功版本。
- 发布完成以数据库实际 `queued/retry/running` 数量为准；达到单轮任务上限时继续后续轮次，不能提前记录成功指纹。
- 单条终态失败进入隔离区；只要队列已排空且候选 Gate/Health 通过，同批正常资料仍可发布，管理器显示 `degraded`。
- 完整异常只进入 Git 忽略的私密日志；网页状态接口移除控制令牌、绝对路径和错误原文。
- 停止操作必须携带匹配实例的本地控制令牌，不根据端口盲目终止其他程序。

### 4.1.1 网页投放

- 只有网站由本机知识管家提供且能力握手严格通过时，网页才显示“投放资料”；GitHub Pages 等纯静态站保持只读和隐藏。
- 浏览器一次上传一个原始二进制文件，最多并行两个；单文件上限取流水线配置和64 MiB中的较小值。
- 上传正文先流式写入 `workspace/data/uploads/staging/`，校验长度并 `fsync` 后，再把全新 UUID 目录原子移动到 `workspace/inbox/files/`；监听器不会看到半文件，同名资料也不会互相覆盖。
- 每份上传按内容哈希关联 SQLite 中的 source/document，状态依次为 `queued → processing → completed`；可重试失败会继续轮询，成功后返回对应文档 ID 并打开知识页。
- 浏览器只在 `sessionStorage` 保存不含令牌的投放回执；刷新不会丢失进度，知识管家重启会把未完成的 `processing` 恢复为 `queued`。
- 网页会话令牌只存在于管理器内存，重启即轮换；它不复用停止服务的控制令牌，也不写日志、状态文件或网站产物。

### 4.2 接收

第一阶段只支持明确输入：

- `workspace/inbox/files/` 中的本地文件
- `workspace/inbox/urls.txt` 中的手工网址

不执行无限递归爬取。

### 4.3 原始保存

- 原始资料只追加、不覆盖。
- 保存来源、时间、MIME 类型和 SHA-256。
- 网页发生变化时保存新版本，不覆盖旧内容。

### 4.4 解析

当前实现使用 Python 标准库解析文本、代码、HTML 与 DOCX；DOCX 设置解压大小和压缩比上限。文本型 PDF 可选调用本机 `pdftotext`，扫描 PDF、OCR 与音视频仍属于后续输入适配器。解析后统一生成标准 Markdown 和 JSON 元数据。

较长的 `.md/.markdown` 在达到固定长度和二级标题数量阈值后，由 `markdown-h2-v1` 确定性拆成知识卡。拆分忽略 YAML frontmatter 与代码围栏中的伪标题，限制最大卡片数，小节过短时合并；原始 raw 永不改写。每张卡记录 `section_index`、`heading_path`、原文起止行、正文 SHA-256 与拆分器版本。

### 4.5 去重

在调用本地模型前完成：

1. 原文件 SHA-256
2. 规范化正文 SHA-256
3. 规范 URL
4. 必要时再做近似重复

重复项建立别名，不直接删除。

### 4.6 逐级分类

模型不能一次自由生成完整路径，而是从根节点逐级选择：

```text
选择一级模块
→ 仅查看该父节点的现有子节点
→ 选择已有子节点或提出新子节点
→ 重复，直到最具体的适合位置
```

分类综合以下信号：

- 专业关键词
- SQLite FTS5 搜索结果
- 本地模型结构化判断
- 相邻资料一致性
- 同名、别名和重复检测

不确定资料进入“待归类”，仍可搜索，不阻塞队列。

### 4.7 知识生成

每份来源可以生成一张或多张知识卡；每张卡至少生成：

- 来源笔记
- 摘要
- 关键观点
- 概念和实体
- 开放问题
- 证据引用
- 主路径
- 辅助关系
- AI 状态与版本

AI 输出默认不是事实。状态包括：

- `personal`
- `unverified`
- `supported`
- `contradicted`
- `deprecated`
- `verified-by-practice`

### 4.8 检查

发布前检查：

- 断链
- 孤立节点
- 重复节点
- 缺失来源
- 无证据关系
- 隐私字段
- 路径冲突
- 过大的网页数据包
- 搜索索引可用性

检查失败时不发布。

## 5. 本地技术架构

### 5.1 核心组件

| 模块 | 默认工具 |
|---|---|
| 运行环境 | macOS 自带 Python 3.9+；仅使用标准库即可运行 |
| 只读领域 API | Java 21、Spring Boot 3.5、Maven |
| 状态与知识关系 | SQLite |
| 中文全文检索 | SQLite FTS5 trigram |
| 本地模型 | 可选 Ollama + Qwen3 8B；不可用时规则引擎自动降级 |
| 文档本体 | Markdown + JSON |
| 前端 | 原生 HTML、CSS、JavaScript |
| 浏览器搜索 | 本地静态 JSON + 原生 JavaScript |
| 知识关系图 | 原生 SVG/Canvas 数据视图 |
| 调度 | 项目内本地知识管家；launchd 仅为可选增强 |
| 版本 | Git |
| 文档导出 | Markdown、JSON、GraphML |
| 虚构公开 Demo | GitHub Pages + GitHub Actions |
| 真实私密知识托管 | Cloudflare Pages + Cloudflare Access |

第一版不要求安装任何依赖，也不采用 Docker、n8n、LangChain、Chroma 或 Neo4j。这样即使没有 Ollama、Node 或 Pandoc，仍可完成确定性的全链路；本地模型只是可选增强。

### 5.2 本地知识管家边界

`apps/pipeline/src/knowledge_os/local_manager.py` 负责收件箱监听、逐文件处理关联、脱敏状态和安全停止；`local_http.py` 提供回环地址上的静态站点及网页投放控制面；`local_inbox.py` 负责大小、文件名、类型、并发、私密暂存和原子发布；`local_manager_cli.py` 负责用户命令和后台进程生命周期。它们只编排现有 `run_full_pipeline`，不复制入库、分类或建站逻辑。

运行状态和日志分别位于 `workspace/data/state/local-manager.json` 与 `workspace/data/logs/local-manager.log`，都属于私密工作区。状态文件权限尽量收紧为当前用户可读写；HTTP 状态响应使用显式字段白名单。知识网站和控制接口只在本机回环地址提供，严格校验 `Host`、浏览器 `Origin` 与临时会话令牌，不提供 CORS；全部响应禁止 iframe，并对管理接口和构建版本禁用浏览器/Service Worker 缓存。端口被其他程序占用时拒绝接管；静态文件解析后必须仍位于生成站点目录内，符号链接不能借此读取项目其他文件。

本功能不安装系统服务、不修改项目外目录，也不依赖 Docker、Node、第三方 Python 包或外部账号。电脑重启后重新双击项目入口即可；未来若用户明确要求开机自启，再单独启用项目已有的可选 launchd 方案。

### 5.3 Java 服务与离线导入边界

`apps/api/` 的 HTTP 服务是现有 Python 流水线的增量消费者；可选 Java CLI 只承担受限的离线文件入队：

```text
Python 主流水线 ───────→ SQLite schema v2 ← Java 21 只读 API
       ↑                         ↑                ↓
       └──任务处理/分类/发布      └── Java 离线导入  仅查询 public
```

- Java HTTP 服务使用 SQLite 只读连接，不迁移、不建表、不修改任务状态。
- Java 读取和离线入队显式兼容 SQLite schema v1/v2；未知、缺失或非数字版本稳定拒绝。SQLite 版本与 canonical/OpenAPI v1 是彼此独立的版本边界。
- Java 离线导入只创建 source、初始 extract 任务和审计事件；不执行后续任务，不开放网络写接口。
- Python 与 Java 写入使用同一个 POSIX 项目锁；Java 单文件导入失败时回滚 SQLite 并清理本次创建的 raw。
- `/api/v1` 响应显式携带 API 版本。
- 知识列表、详情和搜索统一增加 `visibility='public'` 数据库过滤。
- 来源响应只包含类型、文件名和 SHA-256，不返回 `origin`、`raw_path` 或绝对路径。
- 分类树可以读取全部有效分类节点，但不携带用户资料内容。
- J2 搜索优先复用 Python 流水线维护的 SQLite FTS5 trigram 索引，通过 BM25 加权标题、标签、分类路径、摘要和正文。
- 少于3个字符、包含 FTS 语法字符或无 FTS 命中的查询降级为参数化 `LIKE`；通配符转义后按普通文本匹配。
- 搜索响应携带 `[[...]]` 纯文本高亮片段，不返回 HTML；服务会对片段中的 HTML 特殊字符转义。
- 固定2,000条中文资料基准与1秒回归门禁记录在 `docs/benchmarks/java-search.md`。

### 5.4 共享数据与 HTTP 契约

- `packages/contracts/canonical.schema.json` 定义 canonical schema v1；网站构建在写入产物前使用标准库校验器强制验证。
- `packages/contracts/openapi.yaml` 定义 Java 只读 HTTP API v1，作为 Web 联机模式与未来 App 的接口边界。
- Python 与 Java 契约测试共用 `packages/contracts/examples/canonical-v1.json`，样例只含固定虚构内容。
- v1 只允许增加可选字段；删除、改名或改变字段语义必须新增契约主版本。
- canonical v1 的知识条目可选携带 `section_index`、`heading_path`、`source_line_start/end`、`body_sha256` 和 `splitter_version`；旧客户端可忽略这些增量字段。

### 5.5 SQLite 主要表

- `metadata`
- `nodes`
- `sources`
- `documents`
- `placements`
- `relations`
- `jobs`
- `events`
- `documents_fts`

schema v2 将不可变 `sources` 与知识卡 `documents` 建模为 1:N；每张卡仍只有一个主 `placement`，关系和 FTS 均以 document ID 为粒度。未拆分资料及首卡继续使用原 source ID，后续卡使用来源哈希、拆分器版本、序号、标题路径和正文哈希生成稳定 ID。已有 v1 数据库不会隐式升级，必须在项目锁内先创建一致性快照，再运行显式事务迁移；旧 Markdown 来源迁移后重新进入 extract 队列。

### 5.6 Web 与未来 App 适配边界

- `apps/web/src/data-source.js` 提供静态构建和 HTTP API v1 两种读取适配器；现有 PWA 默认使用静态模式，保持离线优先和零在线服务成本。
- `apps/web/src/local-ingest.js` 是独立的本机写入控制面；它只在严格能力探测成功后启用，不能把上传职责塞入只读 `data-source.js`。
- Java HTTP 控制器只依赖应用服务，应用服务再调用只读仓储；客户端不得直接绑定 SQLite schema。
- 未来原生 App 复用 OpenAPI v1，并在客户端适配层实现鉴权、缓存和同步，不复制 Python 处理逻辑。
- 在目标平台、离线编辑和同步策略明确前不创建空移动端工程，以免过早锁定技术栈。

### 5.7 Java 服务部署与可观测边界

- API 镜像使用固定 Temurin 21 补丁版本的多阶段构建，运行阶段只保留 JRE 和可执行 JAR，并使用非 root UID 10001。
- `.dockerignore` 从构建上下文排除 `workspace/`、导出物、站点生成物和 SQLite 文件；真实知识不得进入镜像层。
- Compose 要求显式提供经过一致性验证的 SQLite 快照，只读挂载；容器根文件系统只读、移除全部 Linux capabilities，并启用 `no-new-privileges`。SQLite JDBC 原生库只允许解压到16MB、归 UID 10001 独占的临时挂载，普通 `/tmp` 不放宽执行权限。
- API 通过 SQLite URI `mode=ro&immutable=1` 打开已验证快照，不创建 WAL/SHM sidecar，也不把正在写入的实时数据库当成不可变文件；部署前必须先运行一致性快照流程。
- `/actuator/health/liveness` 只表示进程可响应；`/actuator/health/readiness` 额外检查 SQLite 可读且 schema 为 v1 或 v2。除 health 外不暴露管理端点，也不展示健康详情。
- 数据库健康失败只在服务端记录脱敏原因类别、SQL state 与错误码，不记录数据库路径、查询内容或知识数据。
- 默认端口只绑定 `127.0.0.1`。容器化只是可重复部署单元，不等于公网安全方案；远程访问前必须另行设计 TLS、认证、授权和同步冲突策略。

## 6. 项目目录

项目已经迁移为模块化单仓库：

```text
knowledge/
├── apps/
│   ├── pipeline/       # Python 主写入与唯一任务处理方
│   ├── api/            # Java 21 只读 API + 受限离线导入 CLI
│   └── web/            # 静态网站与 PWA 源码
├── packages/contracts/ # canonical JSON Schema 与 OpenAPI
├── config/             # taxonomy、运行配置模板和本机配置
├── workspace/          # 私密输入、原始资料、SQLite、Vault、私密构建
├── exports/public/     # 用户知识唯一公开候选
├── docs/
├── ops/
├── compose.yaml        # API 本机只读容器编排
├── scripts/
├── 打开知识库.command  # macOS 双击启动本地知识管家
└── tests/e2e/
```

约束：

- 旧脚本入口保持可用；SQLite schema v2 保留未拆分资料/首卡 ID、分类 ID 和原始资料哈希，新增卡使用稳定派生 ID。
- `workspace/` 除说明文件外整体忽略；原始资料只追加。
- `apps/web/src/` 是手写网站源码唯一位置，`workspace/site/` 只是派生产物。
- Python 顶层旧导入由兼容 facade 保留；实现模块以600行为审查上限。
- 本地知识管家只负责编排和服务，不成为第二套流水线；控制状态与日志不得进入 Git。
- 详细边界以 `docs/project-structure.md` 为准。
## 7. 在线访问设计

### 7.1 虚构公开 Demo

GitHub Actions 只检出公开仓库，在隔离临时目录导入 `tests/fixtures/` 中3份固定虚构资料，生成独立 SQLite 和 public 静态站点。只有数据库、构建清单、可见性、隐私、密钥和断链门禁全部通过时，产物才会部署到 GitHub Pages。

公开地址：<https://eascaty.github.io/open-knowledge/>

该工作流不读取维护者电脑，也不能访问真实 `workspace/inbox/`、`workspace/data/state/`、`workspace/vault/` 或 `workspace/site/dist/`。

### 7.2 真实私密知识默认方案

采用 Cloudflare Pages 托管纯静态网站，Cloudflare Access 保护访问。

优点：

- 网站发布后不依赖 Mac 在线。
- 无在线数据库和模型费用。
- 静态访问快。
- 可以回滚历史部署。
- 可使用免费 `*.pages.dev` 地址。

### 7.3 网站数据

线上只上传生成后的站点：

```text
exports/public/
├── index.html
├── assets/
├── data/
│   ├── taxonomy.json
│   ├── modules/
│   ├── search-index/
│   └── graph/
└── reports/
```

不上传：

- 原始 PDF、Word 和账号导出
- SQLite 数据库
- 日志、缓存和隔离文件
- 密钥和部署 Token

### 7.4 私密版与公开版

- 私密版：完整的整理后知识，受 Access 登录保护。
- 公开版：仅包含 `visibility: public` 内容。
- 默认可见性始终为 `private`。

### 7.5 软件版本发布链路

- 正式软件版本只由已经合入 `main` 的语义版本 tag 触发，不接受功能分支提交或人工上传的未验证 JAR。
- `scripts/verify-release` 强制 tag、`pyproject.toml`、Python 运行时、Java POM 和 CHANGELOG 版本一致。
- Release 工作流重新执行 Python、Java、Web、公开 Demo 与容器构建，生成版本化 JAR 和 SHA-256。
- GitHub artifact attestation 为发布文件记录 build provenance；工作流仅申请 Release 和 attestation 所需权限。
- 构建上下文与发布文件白名单都不包含 `workspace/`、SQLite、原始资料、private 站点或凭据。
- 工作流只由显式版本 tag 触发；Dependabot 或普通依赖更新不会自动发布。

### 7.6 网站构建与发布限制

```yaml
publish:
  debounce_minutes: 15
  maximum_deployments_per_day: 10
  deploy_only_when_changed: true
  require_lint_success: true
  rollback_on_healthcheck_failure: true
```

发布步骤：

1. 合并连续变更。
2. 生成临时站点。
3. 本地运行结构、搜索和隐私测试。
4. 成功后发布。
5. 检查线上地址。
6. 失败则保留或恢复上一正常版本。

### 7.7 网站形态

- 一级专业模块入口
- 左侧母子目录
- 中间知识正文
- 右侧来源证据和关联知识
- 全文搜索
- 可点击知识图
- 最近新增与周报
- 手机响应式
- PWA 安装与可选离线缓存
- 最后更新时间和部署版本

## 8. 电脑资源影响

已验证电脑为 M3、16GB 内存，适合单并发运行 Qwen3 4B–8B。

预计首次占用：

| 内容 | 估计空间 |
|---|---:|
| Qwen3 8B | 约5.2GB |
| Whisper（后续） | 约0.5GB |
| Python、Node 与解析工具 | 约1–2GB |
| OCR、构建和缓存 | 约1–3GB |
| 合计 | 约8–12GB |

默认保护：

```yaml
minimum_free_disk_gb: 40
maximum_cache_gb: 5
maximum_log_mb: 200
llm_concurrency: 1
run_heavy_jobs_on_battery: false
allow_system_sleep: true
raw_auto_delete: false
```

无任务时工作进程退出。Mac 睡眠时暂停新资料处理，但线上现有版本继续可用。

## 9. 安全与隐私

- 所有输入默认私有。
- 部署凭据存入 macOS Keychain。
- 站点中不包含密码、Cookie、验证码或 API Token。
- 公开构建采用白名单，不采用排除式发布。
- Cloudflare Access 仅允许精确指定的邮箱。
- 增加 `X-Robots-Tag: noindex` 等安全响应头。
- 原始资料不因云端发布而移动或删除。

以后接入聊天记录时，默认不采集临时聊天，并检测凭据、验证码、完整卡号和其他敏感字段。

## 10. 自动化与可恢复性

提供一个默认不安装的 `launchd` LaunchAgent 模板；用户以后选择启用时，定期执行：

```text
./scripts/run-pipeline
```

要求：

- 文件锁防止任务重叠。
- 每阶段独立事务。
- 指数退避与最大重试次数。
- 单条失败不影响其他任务。
- 每日生成健康报告。
- 数据库创建一致性快照。
- v1→v2 只允许通过 `scripts/migrate` 显式迁移，迁移前自动快照并在提交前检查完整性与外键。
- `scripts/restore-drill` 只把快照恢复到临时候选库并核对哈希、schema、关键表与行数，绝不覆盖正式数据库。
- `scripts/acceptance` 只使用固定虚构资料验证嵌套输入、重复、长文拆卡、队列续跑、失败隔离、Gate/Health 与零网络。
- 依赖与模型不自动升级。

## 11. 实施阶段

### 阶段 A：本地骨架（已完成）

- Python CLI
- SQLite schema
- taxonomy 导入
- 文件收件箱
- SHA-256 去重
- 标准 Markdown

### 阶段 B：本地智能整理（规则引擎已完成，Ollama 可选）

- Ollama
- 结构化摘要
- 长 Markdown 知识卡拆分
- 逐级分类
- 证据关系
- FTS5 搜索

### 阶段 C：网站（已完成）

- 专业树界面
- 全文搜索
- 知识地图
- 报告
- 手机适配
- PWA

### 阶段 D：线上发布（虚构公开 Demo 已完成；真实私密站点待账号配置）

- GitHub Pages 虚构公开 Demo
- GitHub Actions 隔离构建与门禁
- Cloudflare Pages
- Cloudflare Access
- 自动构建
- 健康检查
- 回滚

### 阶段 E：扩展输入（暂缓）

- OCR
- 音视频
- ChatGPT/Gemini
- 浏览器采集
- RSS 与合规网页采集

## 12. 第一版验收标准

1. 投入同一文件两次只生成一份资料。
2. 示例 Java 文档进入正确母子路径。
3. 新增允许层级以下的子节点不会破坏锁定主干。
4. 网站可以浏览金融、AI、技术三条主干。
5. 搜索结果能回到来源和证据。
6. 断链或隐私测试失败时禁止发布。
7. Mac 关机后线上已发布版本仍可访问。
8. 未标记 `public` 的内容不进入公开构建。
9. 任务中断后可以继续。
10. 里程碑更新状态；真实部署、迁移和恢复写入操作日志。
11. 长 Markdown 可以生成多张独立分类的知识卡，且原始文件字节不变。
12. 单条坏资料不阻塞同批正常资料，待处理任务为零前不替换正式站。
13. 备份能够在隔离候选库中恢复验证，错误哈希或损坏快照被拒绝。

## 13. 已知边界

- “免费无限”指没有按次 API 费用，不代表没有硬件、磁盘或免费托管额度。
- 在线访问意味着整理后的站点内容存储在托管平台；原始资料仍只在本地。
- 本地模型分类并非绝对正确，不确定内容必须可追踪和纠正。
- Mac 睡眠时不会处理新资料，但不影响线上旧版本。

## 14. 更新规则

本文件是活文档。任何架构、数据边界、技术选型、分类规则或发布策略变更，必须在同一次工作中更新本文件；只有形成长期取舍时才追加决策，只有部署、迁移、恢复或外部状态变化时才追加操作记录。

## 15. 参考资料

- Cloudflare Pages limits: <https://developers.cloudflare.com/pages/platform/limits/>
- Cloudflare Pages pricing: <https://developers.cloudflare.com/pages/functions/pricing/>
- Cloudflare Access policies: <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>
- Cloudflare Access for `pages.dev`: <https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain>
- GitHub Pages custom workflows: <https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>
- SQLite FTS5: <https://www.sqlite.org/fts5.html>
- Ollama API: <https://docs.ollama.com/api/introduction>
- macOS launchd: <https://support.apple.com/guide/terminal/script-management-with-launchd-apdc6c1077b/mac>
