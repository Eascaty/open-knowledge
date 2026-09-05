# Site

这里保存在线知识库与 PWA 的唯一手写前端源码；构建产物位于根目录 `workspace/site/dist/`。

实现原则：

- 原生 HTML、CSS、JavaScript，无需 Node 或外部依赖
- `data-source.js` 隔离数据访问：默认读取静态构建，也可切换到同源 API v1
- 严格专业母子目录
- 浏览器本地全文搜索
- 可点击辅助知识图
- 本机知识详情可预览、确认和撤销主分类调整；公开静态站自动隐藏写入口
- 本机知识详情可预览并确认可信状态，审核历史由私密服务追加保存
- 本机顶部提供可信审核队列，按风险优先级和状态筛选后直接进入知识卡；公开站自动隐藏
- 响应式与 PWA
- 源码位于 `apps/web/src/`
- 私密构建结果位于 `workspace/site/dist/`
- 只部署经过隐私和断链测试的产物

页面通过 `knowledge-data-source` meta 选择 `static` 或 `api`；API 模式遵循 `packages/contracts/openapi.yaml`。默认静态模式保持离线优先和零在线服务成本。
