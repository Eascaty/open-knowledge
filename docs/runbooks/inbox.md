# Inbox

`workspace/inbox/` 是本机唯一的用户投放入口，除本运行手册外不进入 Git。

计划结构：

```text
workspace/inbox/
├── files/       # PDF、Word、Markdown、文本等
└── urls.txt     # 一行一个手工网址
```

日常使用时先双击根目录的 `打开知识库.command`，然后把文件放进 `workspace/inbox/files/`。后台知识管家确认文件不再变化后自动运行完整流水线；它复制原始资料并按内容哈希去重，不覆盖投放文件。

知识网站固定为 <http://127.0.0.1:8765/>。重复双击只复用现有实例；电脑重启后再双击一次即可。排障时可使用：

```bash
./scripts/knowledge-manager status
./scripts/knowledge-manager stop
./scripts/run-pipeline
```

自动处理失败不会删除或替换上一版正常网站。状态接口只显示脱敏结果，完整错误保存在 Git 忽略的 `workspace/data/logs/local-manager.log`。
