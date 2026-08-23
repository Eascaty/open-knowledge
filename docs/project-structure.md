# 工程目录与模块分工

> 项目根目录：`$HOME/AI/knowledge`

项目采用模块化单仓库：源码按可部署应用分开，共享契约集中管理，所有真实私密数据集中在一个被 Git 忽略的工作区。

```text
knowledge/
├── apps/
│   ├── pipeline/
│   │   └── src/knowledge_os/
│   │       ├── processing/         # 提取、分类、任务处理、导出
│   │       ├── storage/            # schema、记录、队列、taxonomy、搜索
│   │       ├── operations/checks/  # 数据库、隐私、链接、站点和网络检查
│   │       ├── site/build/         # 规范化、payload、PWA 渲染与原子构建
│   │       ├── publish/            # 可选发布适配器
│   │       ├── local_manager.py    # 收件箱监听、站点服务与本地控制
│   │       ├── local_manager_cli.py# 后台进程生命周期与用户命令
│   │       └── cli.py              # 知识命令行入口
│   ├── api/                        # Java 21 只读 API + 受限离线导入 CLI
│   └── web/
│       └── src/                    # HTML、CSS、JavaScript、离线页
├── packages/
│   └── contracts/                  # canonical JSON Schema 与 OpenAPI
├── config/
│   ├── taxonomy.json               # 用户专业树唯一权威
│   ├── runtime.example.json        # 可提交运行配置模板
│   └── runtime.json                # 本机配置，自动创建且不进 Git
├── workspace/                      # 除 README 外整体忽略
│   ├── inbox/files/                # 唯一日常投入入口
│   ├── data/raw/                   # 不可变原始副本
│   ├── data/normalized/            # 标准化文本
│   ├── data/state/                 # SQLite 与项目锁
│   ├── data/quarantine/            # 失败隔离
│   ├── vault/                      # 人类可读知识树
│   ├── site/                       # 私密站点数据与构建
│   └── exports/private/            # 健康报告与一致性备份
├── exports/public/                 # 用户知识唯一公开候选
├── docs/                           # 权威设计、ADR、运行手册和历史报告
├── ops/                            # 可选系统运维模板
├── compose.yaml                    # Java API 的本机只读容器编排
├── scripts/                        # 跨应用稳定入口
├── 打开知识库.command              # macOS 双击入口
└── tests/
    ├── fixtures/                   # 固定虚构样例
    ├── e2e/                        # 跨应用完整闭环
    └── web/                        # 静态/API 数据源适配器测试
```

## 边界与责任

- `apps/pipeline/` 是主写入方；它独占 SQLite schema、任务处理、分类和导出。
- 本地知识管家只编排现有 Python 全流水线并提供上一版正常站点，不重新实现任务处理；状态、令牌和日志只进入私密 `workspace/`。
- `apps/api/` 的 HTTP 服务只读 SQLite schema v1；同模块的可选离线 CLI 只创建 source、初始 extract 任务和审计事件，不迁移、不建表、不处理任务。
- Python 与 Java 写入使用同一个 POSIX 项目锁，禁止并发修改 raw 和 SQLite；Java 导入在单事务失败时回滚数据库并清理新 raw。
- `apps/web/` 是网站源码唯一位置；`workspace/site/` 只保存可重建产物。
- Web 数据源适配器支持静态构建和同源 API v1；Java HTTP 控制器经应用服务访问仓储，UI 与 API 都不直接拥有数据库写入权。
- `packages/contracts/` 描述跨语言数据/API 边界，不包含用户知识。
- canonical v1 在静态构建前强制校验；Java 与 Python 使用同一份虚构契约样例，HTTP 端点以 OpenAPI v1 为准。
- `workspace/` 是真实私密状态；原始资料只追加，不被重构脚本覆盖。
- `exports/public/` 仍是用户知识唯一允许公开发布的候选目录。
- GitHub Pages Demo 只使用 `tests/fixtures/` 的固定虚构资料和 `config/runtime.example.json`。
- `apps/api/Dockerfile` 的构建上下文由 `.dockerignore` 排除整个 `workspace/`、导出物和生成物；镜像不包含任何真实知识。
- `compose.yaml` 只接受显式指定的 SQLite 文件，只读挂载且默认绑定 `127.0.0.1`；它不是公网认证或同步方案。

## 依赖方向

```text
apps/pipeline ──生成──> SQLite + canonical JSON
       │                       │
       ├──使用──> apps/web/src │
       │                       └──读取──> apps/api
       └──校验──> packages/contracts <──消费── Web / future App
```

Python 顶层兼容 facade 暂时保留 `knowledge_os.db`、`knowledge_os.knowledge` 和站点 builder 的旧导入，避免目录迁移破坏现有脚本；新实现直接使用职责子包。

暂不创建空的 `apps/mobile/`。确定 iOS/Android 技术栈与同步需求后，原生客户端应从 OpenAPI v1 生成或实现客户端，并把离线缓存放在 App 自己的适配层。
