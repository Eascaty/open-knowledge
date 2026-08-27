# Inbox

`workspace/inbox/` 是本机唯一的用户投放入口，除本运行手册外不进入 Git。

计划结构：

```text
workspace/inbox/
├── files/       # PDF、Word、Markdown、文本等
└── urls.txt     # 一行一个手工网址
```

日常使用时先双击根目录的 `打开知识库.command`。网站右上角出现“投放资料”后，把文件拖进窗口或点击选择；页面会显示上传、等待整理、正在整理和完成状态，并在成功后打开对应知识。关闭窗口或刷新页面不会中断已经接收的资料。

也可以继续把文件直接放进 `workspace/inbox/files/`。后台知识管家确认文件不再变化后自动运行完整流水线；它复制原始资料并按内容哈希去重，不覆盖投放文件。

知识网站固定为 <http://127.0.0.1:8765/>。重复双击只复用现有实例；电脑重启后再双击一次即可。排障时可使用：

```bash
./scripts/knowledge-manager status
./scripts/knowledge-manager stop
./scripts/run-pipeline
```

自动处理失败不会删除或替换上一版正常网站；可重试错误会继续自动尝试。状态接口只显示脱敏结果，完整错误保存在 Git 忽略的 `workspace/data/logs/local-manager.log`。

“投放资料”没有出现时，依次确认：

1. 当前地址是 `http://127.0.0.1:8765/` 或 `http://localhost:8765/`，而不是 GitHub Pages 公开 Demo。
2. 重新双击 `打开知识库.command`，再刷新网页。
3. 运行 `./scripts/knowledge-manager status` 查看知识管家是否为 `running`。

网页上传最多同时接收两个文件，单个文件最大64 MiB（若运行配置更小，则以配置为准）。接收中的半文件只存在于私密暂存区，完整写入后才原子进入 inbox。上传会话令牌仅保存在进程内存中，不写入网页、Git 或本地状态文件。
