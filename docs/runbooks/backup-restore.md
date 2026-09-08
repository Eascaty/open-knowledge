# 本地备份、分享包、恢复演练与迁移手册

本手册涵盖 SQLite 快照、完整私密备份包、站点分享包及数据库恢复演练。原始资料、Vault 和网站都是独立目录；不要把复制数据库文件当作整个项目的完整备份。数据库恢复演练只验证快照；整包恢复使用下文的 `restore-backup-bundle`，仅写入全新目录。

## 日常备份

在项目根目录运行：

```bash
./scripts/backup
```

该命令会取得项目写锁，通过 SQLite online backup API 生成一致性快照，执行完整 `integrity_check`，再以 `0600` 权限原子落盘。结果会输出快照路径、SHA-256、大小和创建时间。默认位置是：

```text
workspace/exports/private/backups/
```

不要直接复制正在使用的 `knowledge.sqlite3`、`-wal` 或 `-shm` 文件作为备份。

如果需要同时保存数据库之外的原始资料、Vault 和已构建站点，可创建完整项目备份包：

```bash
./scripts/backup-bundle
```

默认输出到 `workspace/exports/private/project-backups/`。备份包包含 SQLite 一致性快照、
配置、inbox、raw、normalized、quarantine、Vault、站点数据和站点构建，并写入逐文件
SHA-256 清单；不包含上传凭据，也不会联网。归档先写入私密临时候选，自动通过逐文件摘要与 SQLite 验证后才原子发布最终 ZIP；验证失败会清理候选，已有成功备份保持不变。自动核验会额外读取归档并检查数据库，耗时随备份大小增加。

复制或存放备份包后，仍可再次离线核验，以发现后续损坏：

```bash
./scripts/verify-backup-bundle /absolute/path/to/knowledge-backup.zip \
  --sha256 <备份时记录的64位SHA-256>
```

核验只读取压缩包并在临时目录检查 SQLite 完整性，不会解压覆盖项目文件，也不会修改正式数据库。

## 完整备份恢复到新目录

使用与备份 schema v2 兼容的当前源码，在项目根目录运行：

```bash
./scripts/restore-backup-bundle /absolute/path/to/knowledge-backup.zip \
  /absolute/path/to/new-knowledge-data \
  --sha256 <备份时记录的64位SHA-256>
```

目标目录必须尚不存在，父目录必须已存在；即使目标是空目录或符号链接也拒绝覆盖。摘要参数可省略，但建议使用备份时另行保存的值；仅靠包内清单不能证明归档来自可信来源。

恢复过程在目标父目录的私密临时候选中复制并验证归档，按允许的数据路径解包，拒绝路径穿越、特殊文件及大小写冲突；核对数据库引用的 raw/normalized 文件、原件摘要和搜索索引卡片集合。随后保留数据库内容及人工记录，使用当前程序重建私密网站，执行 Gate/Health，再独占创建目标目录并移入验证结果。最后写入 `restore-result.json` 完成标记，输出 `ok: true`。

恢复不会重新扫描收件箱、提炼知识、调用 Ollama、启动服务、上传资料或自动迁移数据库。原件和数据库快照保持字节一致；分类、审核说明与历史由数据库保留。Vault 索引和网站是重建的派生产物，旧网站脚本不会继续使用。WAL/SHM 只由候选 SQLite 按需重新生成，不恢复管理器会话、锁或上传凭据。schema v1 包需先按迁移手册显式迁移并重新备份。

普通验证、磁盘或重建失败会清理候选和本次创建的目标，已有目标与原库保持不变。落盘涉及多个目录移动，强制结束进程或断电可能留下候选或未完成目标；没有完成标记的目录不能视为恢复成功。此时保留原备份，选择另一个新目录重试，核对后再人工清理残留。

恢复结果是数据目录，尚不包含独立安装程序。可用当前源码中的管理器指向它（确保未与另一实例使用相同端口）：

```bash
PYTHONPATH=apps/pipeline/src python3 -B -m knowledge_os.local_manager_cli \
  --root /absolute/path/to/new-knowledge-data start
```

确认完成标记后再启动；启动管理器会按正常规则处理恢复的待导入资料。需要原样查看恢复结果时，可先仅阅读 Vault。恢复不替换现有项目；真实换机或运维恢复完成后，按项目规则人工记录备份摘要、结果和验证范围，勿记录私密正文或凭据。

## 生成可分享站点包

如果只想把已经构建好的知识网站放到自己的云盘或静态托管，运行：

```bash
./scripts/package-site --visibility private
```

命令会先检查 `workspace/site/dist` 的构建清单和可见性，再在
`workspace/exports/private/site-packages/` 生成带 `knowledge-site-package.json`
清单和 SHA-256 的 ZIP。包里只有站点文件，不包含 SQLite、原始资料、Vault、日志、
运行配置或上传凭据；命令本身不联网、不上传。把 ZIP 解压到你选择的静态托管目录后，
仍应由你自行配置访问控制，不能把 `private` 包当成认证方案。

如果准备使用 Cloudflare Pages，可以先检查发布计划：

```bash
./scripts/publish-plan \
  --project-name your-pages-project \
  --visibility private
```

该命令只执行本地数据库、站点、隐私和断链门禁，输出待执行的 `wrangler` 命令，
不会登录 Cloudflare、联网或上传文件。只有确认 Cloudflare Access 已启用后，才允许
在独立的发布流程中执行真实上传。

拿到 ZIP 后可用以下命令核对整体和逐文件摘要；命令只读取压缩包，不会解压：

```bash
./scripts/verify-site-package /absolute/path/to/site-package.zip \
  --sha256 <package-sha256>
```

## 恢复演练

恢复演练只在临时目录创建候选数据库，不覆盖正式数据库：

```bash
./scripts/restore-drill /absolute/path/to/snapshot.sqlite3 \
  --sha256 <备份时记录的64位SHA-256>
```

成功报告必须同时满足：

- SHA-256 与预期一致；
- SQLite 完整性检查为 `ok`；
- 外键异常为 0；
- schema 为受支持的 v1 或 v2；
- metadata、sources、documents、placements、relations、jobs、events 和 FTS 表完整；
- 临时候选库与快照的关键表行数一致；
- `live_database_modified` 为 `false`。

符号链接、损坏文件、错误哈希、缺表和未知 schema 都会被拒绝。演练成功不代表已切换正式数据库。

## SQLite schema v1 升级到 v2

schema v2 把 `source → document` 从一对一改为一对多，用于把一份长 Markdown 分成多张可独立分类的知识卡。迁移不会修改不可变 raw。

先停止知识管家，再执行：

```bash
./scripts/knowledge-manager stop
./scripts/migrate
./scripts/knowledge-manager start
./scripts/kb status
./scripts/doctor
```

`./scripts/migrate` 会先自动生成并验证 `knowledge-pre-schema-v2-*` 快照，然后在单一事务中迁移。保存命令输出中的快照路径和 SHA-256，并可先用 `restore-drill` 验证这份迁移前快照。旧 document ID、分类、事件和 FTS 内容会保留；现有 Markdown source 会安全重置到 extract 阶段，让旧长文在知识管家重启后实际拆卡，报告中的 `requeued_sources` 会给出数量。失败时数据库回滚到完整 v1。再次运行是幂等的，不会重复迁移、重复排队或重复备份。

`scripts/acceptance` 是只使用虚构临时数据的工程回归入口，可以在维护前后单独运行，但它不会代替对真实迁移库执行 `kb status` 和 `doctor`。

不要绕过 `./scripts/migrate` 手工修改 `metadata.schema_version`。

## 真正恢复正式数据库

当前版本故意不提供“一键覆盖正式库”命令。真实恢复是有数据丢失风险的运维操作，必须在单独确认后按以下门禁执行：

1. 停止知识管家以及所有 Python/Java 写入任务。
2. 若现库仍可读，先为现库创建一份紧急一致性备份。
3. 对目标快照执行带 SHA-256 的 `restore-drill`。
4. 明确恢复点与现库之间会丢失哪些 source、document 和任务记录。
5. 在同一磁盘创建候选正式库，再次执行完整性、外键、schema 和关键行数核对。
6. 由操作者明确确认后，才原子切换数据库；旧库和 WAL/SHM 作为带时间戳的隔离副本保留，不直接删除。
7. 启动知识管家，依次检查 `kb status`、`doctor`、搜索和网站；异常时立即切回隔离的旧库。
8. 把实际恢复时间、快照哈希、切换结果和回滚结果追加到 `logs/operations.md`。

恢复演练属于只读验证，不写运维日志；真实迁移、正式恢复或部署切换才写运维日志。
