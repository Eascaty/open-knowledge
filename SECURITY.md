# 安全策略

## 支持范围

安全修复优先应用于默认分支的最新版本。项目目前处于早期开发阶段，尚未承诺长期支持旧版本。

## 报告漏洞

请不要用公开 Issue 报告可能导致私人知识、凭据或本地文件泄露的漏洞。请使用仓库 `Security` 页面中的 [Report a vulnerability](https://github.com/Eascaty/open-knowledge/security/advisories/new) 私密提交；如果该功能暂时不可用，请通过维护者 GitHub 主页联系并只描述最小复现信息。

报告中请包含：

- 受影响版本或提交；
- 最小复现步骤；
- 可能暴露的数据范围；
- 建议的缓解方法（如有）。

不要附带真实私人笔记、Token、Cookie、账号导出或数据库副本。

## 安全边界

- 所有知识默认标记为 `private`。
- 只有显式生成的公开构建允许对外发布。
- 原始资料、SQLite 数据库、缓存和私密导出由 `.gitignore` 隔离。
- 默认流程不调用网络服务，也不使用付费 API。
- Java API 默认只监听 `127.0.0.1`，并且只查询 `public` 内容。
- `main` 禁止强推与直接提交，必须通过受 CI 保护的 Pull Request。
