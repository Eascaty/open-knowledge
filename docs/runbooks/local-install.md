# Open Knowledge 本地版安装、升级与卸载

本地包包含程序、网页、数据契约和一份虚构示例。需要 **Python 3.9 或更高版本（含 SQLite/FTS5）** 与浏览器；不需要 Git、Java、Docker、账号或额外 Python 依赖。主要使用环境为 macOS，Linux 可使用终端启动；Windows 原生运行尚未支持。此包未内置 Python，也不是经过签名或公证的商店安装程序。

## 下载与检查

正式发行时下载 `knowledge-local-<版本>-<提交>-<内容摘要>.tar.gz` 及同名 `.sha256` 文件。以 Release 标签和版本说明判断正式版本；本地构建包不是新的正式 Release。`local-package.json` 记录版本、基线提交、程序内容摘要及逐文件清单；源码工作区可能包含基线后的改动，包内实际内容以逐文件摘要为准。

macOS 可在下载目录校验：

```sh
shasum -a 256 -c knowledge-local-<版本>-<提交>-<内容摘要>.tar.gz.sha256
```

Linux 使用 `sha256sum -c`。只使用自己构建或可信发行页面的包；摘要用于检查下载是否损坏。

## 首次使用

1. 解压到自己的可写目录，保留包内目录结构。不要放进公开网站目录。
2. 在终端运行 `python3 --version` 确认 Python；如果缺少，请先安装兼容 Python。程序不会自动下载运行时或模型。
3. macOS 双击 `打开知识库.command`；Linux 在解压后的目录执行 `sh scripts/knowledge-manager start`。系统阻止打开时，先核对来源与摘要，再从该目录的终端使用同一命令；不要关闭系统安全保护。
4. 在打开的本机网页中投放 `examples/java_g1.md`，然后搜索“Java”、打开知识卡。示例是虚构资料，私人文件需由你自行选择。
5. 使用完毕运行 `sh scripts/knowledge-manager stop`。再次启动会继续使用这个目录中的数据。

如果浏览器未自动打开，运行 `sh scripts/knowledge-manager status --json` 查看本机入口；端口占用时先停止旧实例，或通过 `start --port 8766` 选择空闲端口。首次使用不会调用模型或付费服务；可选本地模型需要你显式配置。

程序与数据相邻：首次使用才创建 `config/` 和 `workspace/`。后者包含原件、数据库、Vault、网站和私密备份；切勿将整个已使用目录作为公开程序包分享。重新分发请使用干净的发行附件。

## 升级：在新目录验证后再切换

不要把新包直接覆盖到正在运行的旧目录。

1. 停止旧知识管家，用旧版本的 `sh scripts/backup-bundle` 生成完整备份，保存返回的摘要。
2. 将新程序包解压到另一个目录，阅读对应版本说明；不要先在新目录启动知识管家。
3. 在新程序目录执行 `sh scripts/restore-backup-bundle /备份包路径 /尚不存在的数据目录 --sha256 <摘要>`。
4. 确认数据目录中的 `restore-result.json` 为 `verified` 后，用新程序指向该目录：

```sh
PYTHONPATH=apps/pipeline/src python3 -B -m knowledge_os.local_manager_cli \
  --root /恢复数据目录 start
```

5. 确认原件、知识卡、审核和搜索符合预期后再继续导入。保留旧程序和备份作为回退点。若已在新目录写入资料，旧目录不会自动得到这些变更；不能把直接切回旧目录当作同步。

数据独立存放后，日常启动、状态和停止都需要使用上述模块入口及同一个 `--root`；双击入口仍指向程序自己的目录。完整恢复说明见 `docs/runbooks/backup-restore.md`。当前恢复支持 schema v2；涉及旧 schema 的升级必须按版本迁移说明处理，不自动迁移。

## 卸载与数据取回

先停止对应知识管家。需要保留知识时，先制作完整备份并离线核验，保存到你已有的其他安全存储位置。原件与 Markdown Vault 也可直接读取。

删除程序目录会同时删除其中的 `workspace/` 和 `config/`；如果选择了独立数据目录，删除程序不会删除那份数据。确认备份可恢复后再人工删除不需要的目录。程序不会安装系统服务，也不会自动清理你的原始资料。
