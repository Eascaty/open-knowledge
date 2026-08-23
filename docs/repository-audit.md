# GitHub 发布后缺口审计

- 日期：2026-08-23（Asia/Shanghai）
- 仓库：`Eascaty/personal-knowledge-os`
- 审计范围：开源元数据、分支策略、安全功能、依赖维护、构建复现、CI、Release 与访客体验
- 结果：仓库安全基线已在 `v0.3.1` 建立；J2/J3/J4 与 v0.4.0 可复现发布链路随后完成。

## 已补齐

- `main` 必须通过 Pull Request，Java 21 与 Python 3.9/3.12/3.13 为必需检查。
- 管理员同样受保护，禁止强推和删除主分支；仅允许普通合并以保留分步提交。
- Secret Scanning、Push Protection、Dependabot 安全更新、私密漏洞报告与 CodeQL 已启用。
- Maven Wrapper 固定 Maven 3.9.11，并校验分发 SHA-256。
- `.gitattributes` 固定 shell 为 LF、Windows cmd 为 CRLF，避免跨平台入口损坏。
- Java CI 增加10分钟超时、重复运行取消、80%指令与60%分支覆盖率门槛。
- Dependabot 不再自动把 Spring Boot 3 升级到4；跨大版本迁移必须单独设计和验证。
- Python 与 Java 版本统一为 `0.3.1`，正式 JAR 不再带 `SNAPSHOT`。
- CODEOWNERS、仓库 topics、J2 里程碑和安全 Issue 导航已补齐。

## 已验证

- Java：7/7 测试通过。
- Python：18/18 测试通过。
- JaCoCo：指令88.2%、分支67.5%。
- doctor：数据库、隐私、密钥和断链检查 PASS。
- Git 公开归档不包含真实知识、SQLite、私密导出或生成站点。
- GitHub Dependabot 安全告警：0。
- v0.3.1 Release 已包含正式 Java JAR 与 SHA-256 校验文件。
- v0.4.0 发布链路只接受 `main` 上版本一致的 tag，并重新执行全部核心门禁。
- 正式 JAR 与 SHA-256 由 GitHub Actions 生成，同时建立 build provenance attestation。
- 非 root 容器构建和真实 schema v1 只读冒烟已纳入 CI；`Container Smoke` 将作为 `main` 必需检查。
- GitHub Pages 公开 Demo 已从固定虚构资料自动构建并上线，不包含维护者本机知识。

## 有意留到后续

- 真实私密站点仍需用户确认账号和访问范围后再配置 Cloudflare Pages + Access。
- Java API 的公网认证、TLS 与同步协议需等待真实部署和 App 需求，不由容器化自动推导。
- 原生 App 需先确认目标平台、离线编辑和同步冲突策略。
- 大型综合笔记按标题拆成多知识卡片。
