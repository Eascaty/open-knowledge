# 测试报告

- 日期：2026-08-23（Asia/Shanghai）
- 运行环境：macOS、Python 3.9.6、SQLite FTS5 trigram、Temurin JDK 21.0.12、Maven 3.9.11
- 结果：32 项 Python 测试全部通过；本次未重复运行 Java、Web 和 Linux 容器门禁，其最近记录仍为34项 Java、2个 Web 场景与 J4 Linux 容器冒烟通过
- 正式目录复验：`$HOME/AI/knowledge`
- 最终健康检查：PASS；隐私问题0；断链0；网络请求0
- 浏览器验收：桌面1280×720与手机390×844布局正常，分类导航可交互，控制台0错误
- Java HTTP 冒烟：真实 SQLite 健康状态 `UP`，schema v1、分类树和 public-only 列表响应正确
- Java J3 覆盖率：指令89.4%、分支74.1%；CI 最低门槛保持80%与60%
- Java J2 搜索基准：固定2,000条 public 中文资料、200条命中、10次预热与50次测量；中位数1.325ms、p95 2.260ms，1秒回归门禁通过
- Maven Wrapper：固定 Maven 3.9.11 与 SHA-256，首次下载和完整 `verify` 通过
- 公开 Demo：仅3份固定虚构资料，独立临时 SQLite，9项任务完成，public 门禁 PASS，网络请求0
- Pages 验收：HTTPS 200；线上桌面与手机布局正常，搜索、详情和关系地图可交互，控制台0错误

## 自动测试范围

- raw 不可变与 SHA-256 幂等去重
- Java/G1 深层母子分类
- 未知资料进入“待归类”
- 非法跨级模型路径被拒绝
- 失败重试和最终隔离
- 分类节点退休后的自动迁移
- symlink 输入与 raw 路径越界
- 收件箱到网站的完整自动化
- 本地知识管家首次等待页、收件箱稳定指纹与自动更新
- 后台重复启动复用同一实例，并只能通过匹配控制令牌安全停止
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
- Java SQLite schema v1 只读查询和分类树构建
- Java API 过滤 private 条目并删除来源绝对路径
- Java 搜索使用 FTS5 trigram 与 BM25；覆盖中文短语、短查询降级、无命中降级、SQL/FTS 特殊字符、稳定分页与排序
- 搜索高亮使用纯文本标记并转义 HTML 特殊字符；FTS 与 LIKE 路径均结构性过滤 private 内容
- OpenAPI v1 搜索结果契约包含标题高亮、命中片段和相关度，保留摘要字段兼容 Web 与未来 App
- Python 包、Java JAR、运行时 `__version__` 与 CHANGELOG 发布版本自动一致性检查
- Java 离线导入的哈希幂等、raw 修复、symlink/大小/越界拒绝、篡改检测、事务回滚与项目锁竞争
- Maven `verify` 构建与 JaCoCo 覆盖率报告
- Maven Wrapper 下载校验、CI 覆盖率失败门禁、10分钟任务超时
- Spring Boot 实际启动、真实 SQLite 只读连接与 HTTP 响应
- Actuator liveness 与数据库/schema v1 readiness；数据库不可用时返回503
- 非 root 容器、只读根文件系统、只读 SQLite 挂载、无 capabilities 与数据库哈希不变
- SQLite JDBC 原生库使用专用受限 tmpfs；普通 `/tmp` 与数据库挂载不获得执行权限
- readiness 失败时 CI 输出容器 UID、挂载权限和脱敏 SQL 状态，响应仍不公开数据库详情
- SQLite 快照通过 `mode=ro&immutable=1` 打开，不创建或依赖 WAL/SHM sidecar

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
./scripts/java-test
./scripts/build-demo
```
