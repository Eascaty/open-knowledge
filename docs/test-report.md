# 测试报告

- 日期：2026-09-06（Asia/Shanghai）
- 运行环境：macOS、Python 3.9.6、SQLite FTS5 trigram、Temurin JDK 21.0.12、Maven 3.9.11
- 结果：当前变更可在沙箱运行的 89 项 Python 测试与36个 Web 场景全部通过；完整 Python 套件共113项，其中24项本机回环 HTTP 测试因当前沙箱禁止端口绑定，需在本机环境复验；Java 38项测试在隔离 JDK 21 环境通过，J4 Linux 容器冒烟由远程 CI 复验
- 正式目录复验：`$HOME/AI/knowledge`
- 隔离验收健康检查：PASS；Gate 允许；网络请求0；真实私密数据库已在独立维护窗口完成 v1→v2 快照、迁移、恢复演练和任务排空
- 浏览器布局：本机知识库实际完成桌面交互与390×844手机验收；粘贴区展开、空值禁用、输入启用、字数更新和无横向溢出均通过，验收内容未提交
- 相关知识界面：正式库“Java基础”实际生成5条带原因的推荐，点击后正确进入目标卡片并更新地址；浏览器错误日志为0
- 人工归类界面：独立虚构私密库完成选择、预演、确认、候选站重建、原卡回开和二次确认撤销；桌面与390×844手机无横向溢出，根节点不在选项中，浏览器错误日志为0
- 可信审核界面：本地同源会话支持状态选择、预演、乐观并发确认和候选站重建；公开站不探测接口，状态历史由追加事件保留
- 可信审核说明：覆盖说明长度、同状态说明变更、旧说明并发校验、私密 canonical 历史和 public 构建剥离
- 可信审核队列：正式私密库实际汇总24条待验证知识，状态计数、风险优先级、稳定排序、状态筛选与知识卡跳转通过；队列关闭后正确进入原有预览确认控件，全程未写审核数据
- Markdown 阅读器：覆盖标题目录、列表、引用、表格、代码围栏、外链协议校验、脚本/图片降级、长文分批和正文下载文件名；页面内容均通过 DOM 安全写入
- Java SQLite 兼容：schema v1/v2 健康与导入测试通过，缺失、非数字和未知版本稳定拒绝
- Java 覆盖率：指令89.5%、分支76.5%；CI 最低门槛保持80%与60%
- Java J2 搜索基准：固定2,000条 public 中文资料、200条命中、10次预热与50次测量；中位数1.325ms、p95 2.260ms，1秒回归门禁通过
- Maven Wrapper：固定 Maven 3.9.11 与 SHA-256，首次下载和完整 `verify` 通过
- 公开 Demo：仅3份固定虚构资料，独立临时 SQLite，9项任务完成，public 门禁 PASS，网络请求0
- Pages 验收：HTTPS 200；线上桌面与手机布局正常，搜索、详情和关系地图可交互，控制台0错误
- 网页投放 E2E：真实临时知识管家完成 `session → upload → queued/processing → completed → document.id → site-data`，浏览器令牌未进入公开状态或站点文件
- 公开 Demo 复验：3份虚构资料、9项任务、发布门禁 PASS、网络请求0；投放入口默认隐藏
- 批量虚构验收：5个输入、4个唯一 source、6张知识卡、3张拆分卡；第二次运行5个重复，1个故意坏资料隔离且正常资料保留，Gate/Health PASS，网络请求0

## 自动测试范围

- raw 不可变与 SHA-256 幂等去重
- 长 Markdown H2 拆卡、frontmatter/代码围栏忽略、卡片上限合并、稳定 ID、独立分类和重跑产物收敛
- schema v1→v2 快照前置、事务回滚、FK/完整性校验、旧 Markdown 重排队与重复迁移幂等
- 快照 SHA-256、v1/v2 临时候选恢复、关键表/行数核对、损坏快照拒绝且正式数据库字节不变
- Java/G1 深层母子分类
- 未知资料进入“待归类”
- 非法跨级模型路径被拒绝
- 失败重试和最终隔离
- 分类节点退休后的自动迁移
- symlink 输入与 raw 路径越界
- 收件箱到网站的完整自动化
- 本地知识管家首次等待页、收件箱稳定指纹与自动更新
- 网页投放能力握手、静态公开站自动隐藏、多文件队列、粘贴文字转 Markdown、上传进度、刷新恢复和完成后文档定位
- 相关知识按同一来源、共同标签、同一节点和相邻方向确定性排序；无关一级模块、当前文档和畸形输入不会进入结果
- 人工归类支持预演、旧节点并发校验、活动目标节点与根节点限制，并原子更新 placement、FTS 路径和审计事件；网页覆盖同源会话、请求上限、先预览后确认、忙碌续跑、公开站隐藏和限时撤销
- 可信状态审核支持预演、旧状态并发校验、允许值限制、追加审计事件和 canonical 最新状态导出；网页覆盖同源会话、严格响应、先预览后确认和公开站隐藏
- 可信审核队列对未知状态安全降级为待验证，按争议、过时、待验证优先排列，过滤缺少文档 ID 的畸形输入，并保持公开站隐藏
- Markdown 阅读器拒绝 javascript/data/带凭据的链接，代码围栏中的 HTML 不执行，未支持的 HTML 仅作为文本显示，下载内容不包含来源私密字段
- 阅读历史只保存有限数量的知识卡 ID/时间，畸形或失效记录安全忽略；复制链接/正文优先使用 Clipboard API，失败时使用短暂本地回退，不写入服务端
- ChatGPT/Gemini 导入覆盖当前分支、会话/活动降级解析、HTML 文本提取、ZIP 路径穿越/压缩比/大小上限、稳定文件名和重复导入；导入输出仅落在 workspace 内
- Ollama 提炼器覆盖合规 JSON、错误结构、关系置信度边界、响应大小上限和网络失败自动回退规则引擎
- 站点分享包覆盖构建清单/可见性校验、ZIP 文件清单、路径穿越/符号链接拒绝、整体 SHA-256、逐文件摘要验签和重复打包收敛
- 浏览器会话令牌、Host/DNS rebinding、Origin、OPTIONS/CORS、CSP、iframe与响应头边界
- 上传半文件只进入未监控暂存区，中断清理、同名不覆盖、目录0700、文件0600、大小/类型/路径校验
- manager 重启恢复未完成状态并轮换浏览器令牌，令牌不写状态文件或静态站点
- retry 轮、Gate/Health 失败和原子发布异常均保留上一版正式站，并清理候选目录
- 数据库实际 pending/failed 状态决定退出码；单条入库失败继续处理后续资料，终态坏资料不阻塞已检查的正常站点
- 迁移或修复只重排数据库任务、收件箱指纹未变化时，知识管家启动后仍会自动续跑；本轮曾重试但最终 pending 为0时会发布候选站
- Service Worker 不缓存本地控制接口、构建版本或 private 数据；前端资源变化会更新缓存版本
- DOCX 正常解析与高压缩比压缩炸弹拒绝
- 后台重复启动复用同一实例，并只能通过匹配控制令牌安全停止；启动验证失败会终止本次子进程并恢复上一份私密状态
- 流水线失败时保留上一版正常网站，公开状态不泄露令牌、绝对路径或异常原文
- 本地网站拒绝通过生成目录中的符号链接读取站点目录外文件
- GitHub Pages Demo 仅使用固定虚构资料且不包含用户主目录路径
- 再次运行不重复建库
- 项目锁竞争
- SQLite WAL 一致性快照
- 凭据扫描且报告不泄露凭据值
- 断链检查零网络请求
- private 候选包不能冒充 public 发布
- public 构建剔除 private 内容和 URL 查询参数
- private noindex、JSON no-store 和 PWA 不缓存知识数据
- 坏 canonical 不替换上一正常站点
- Java API v1 响应契约与分页参数
- Java DTO 与 canonical JSON 核心字段一致性
- canonical JSON Schema 强制校验与 OpenAPI v1 路由契约
- Web 静态包/API v1 数据源适配器与生成产物加载顺序
- Java 控制器、应用服务和只读仓储分层边界
- Java SQLite schema v1/v2 只读查询、离线入队和分类树构建
- Java API 过滤 private 条目并删除来源绝对路径
- Java 搜索使用 FTS5 trigram 与 BM25；覆盖中文短语、短查询降级、无命中降级、SQL/FTS 特殊字符、稳定分页与排序
- 搜索高亮使用纯文本标记并转义 HTML 特殊字符；FTS 与 LIKE 路径均结构性过滤 private 内容
- OpenAPI v1 搜索结果契约包含标题高亮、命中片段和相关度，保留摘要字段兼容 Web 与未来 App
- Python 包、Java JAR、运行时 `__version__` 与 CHANGELOG 发布版本自动一致性检查
- Java 离线导入的哈希幂等、raw 修复、symlink/大小/越界拒绝、篡改检测、事务回滚与项目锁竞争
- Maven `verify` 构建与 JaCoCo 覆盖率报告
- Maven Wrapper 下载校验、CI 覆盖率失败门禁、10分钟任务超时
- Spring Boot 实际启动、真实 SQLite 只读连接与 HTTP 响应
- Actuator liveness 与数据库/schema v1/v2 readiness；数据库不可用或版本不受支持时返回503
- 非 root 容器、只读根文件系统、只读 SQLite 挂载、无 capabilities 与数据库哈希不变
- SQLite JDBC 原生库使用专用受限 tmpfs；普通 `/tmp` 与数据库挂载不获得执行权限
- readiness 失败时 CI 输出容器 UID、挂载权限和脱敏 SQL 状态，响应仍不公开数据库详情
- SQLite 快照通过 `mode=ro&immutable=1` 打开，不创建或依赖 WAL/SHM sidecar
- Release 门禁验证 tag、Python、Java、运行时和 CHANGELOG 版本一致，并要求发布提交属于 `main`
- 正式 JAR 生成 SHA-256 与 GitHub build provenance；发布工作流不读取 `workspace/` 或私密产物
- PR #28 远程验证全部成功：Java 21 两次构建 JAR 哈希一致，Container Smoke、三套 Python、Public Demo 与 CodeQL 三语言全部通过

## 端到端结果

```text
3 个输入
→ 3 个 source
→ 9 个 extract/enrich/index 作业
→ 9 个成功，0 个失败
→ 3 篇知识文档
→ 25 个分类节点
→ 静态网站与发布门禁 PASS
```

分类结果：

```text
AI Agent 的长期记忆
→ AI / Agent / 智能体

信用卡权益与美股消费场景
→ 金融 / 财经 / 信用卡 / 美股

Java G1 垃圾回收器排障笔记
→ 技术 / 程序员 / Java开发 / JVM / 垃圾回收 / G1
```

验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=apps/pipeline/src \
python3 -B -m unittest discover -s tests -v

zsh -n scripts/*
plutil -lint ops/launchd/com.local.knowledge-os.plist.template
./scripts/test-web
./scripts/acceptance
./scripts/java-test
./scripts/build-demo
```
