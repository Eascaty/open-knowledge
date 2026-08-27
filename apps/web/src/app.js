"use strict";

const state = {
  data: null,
  search: null,
  graph: null,
  nodes: new Map(),
  documents: new Map(),
  children: new Map(),
  documentsByNode: new Map(),
  expanded: new Set(),
  activeNodeId: null,
  activeDocumentId: null,
  view: "node",
  graphHitboxes: [],
};

const ui = {};

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "className") {
      node.className = value;
    } else if (name === "text") {
      node.textContent = value;
    } else if (name === "dataset") {
      for (const [key, item] of Object.entries(value)) node.dataset[key] = item;
    } else if (name.startsWith("aria-")) {
      node.setAttribute(name, String(value));
    } else if (name === "hidden") {
      node.hidden = Boolean(value);
    } else {
      node.setAttribute(name, String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

function formatDate(value) {
  if (!value) return "尚未记录更新时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(status) {
  const labels = {
    personal: "个人认知",
    unverified: "待验证",
    supported: "有证据支持",
    contradicted: "存在争议",
    deprecated: "已过时",
    "verified-by-practice": "实践验证",
  };
  return labels[status] || status || "待验证";
}

function relationLabel(relation) {
  return relation.label || relation.type || "相关";
}

function safeHttpUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.href;
  } catch {
    return null;
  }
}

function pathText(path) {
  return Array.isArray(path) ? path.join(" / ") : "";
}

function getNode(nodeId) {
  return state.nodes.get(nodeId) || state.nodes.get(state.data.root);
}

function getNodeDocuments(nodeId, includeDescendants = false) {
  if (!includeDescendants) return state.documentsByNode.get(nodeId) || [];
  const ids = new Set();
  function collect(currentId) {
    for (const doc of state.documentsByNode.get(currentId) || []) ids.add(doc.id);
    for (const childId of state.children.get(currentId) || []) collect(childId);
  }
  collect(nodeId);
  return [...ids].map((id) => state.documents.get(id));
}

function nodeAncestors(nodeId) {
  const result = [];
  let current = state.nodes.get(nodeId);
  while (current) {
    result.unshift(current);
    current = current.parent_id ? state.nodes.get(current.parent_id) : null;
  }
  return result;
}

function isDescendant(nodeId, ancestorId) {
  let current = state.nodes.get(nodeId);
  while (current) {
    if (current.id === ancestorId) return true;
    current = current.parent_id ? state.nodes.get(current.parent_id) : null;
  }
  return false;
}

function setupIndexes() {
  for (const node of state.data.nodes) {
    state.nodes.set(node.id, node);
    state.children.set(node.id, [...(node.children || [])]);
    state.documentsByNode.set(node.id, []);
  }
  for (const documentItem of state.data.documents) {
    state.documents.set(documentItem.id, documentItem);
    if (!state.documentsByNode.has(documentItem.node_id)) {
      state.documentsByNode.set(documentItem.node_id, []);
    }
    state.documentsByNode.get(documentItem.node_id).push(documentItem);
  }
  for (const documents of state.documentsByNode.values()) {
    documents.sort((left, right) => {
      const byDate = String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
      return byDate || left.title.localeCompare(right.title, "zh-CN");
    });
  }
}

function topModules() {
  return (state.children.get(state.data.root) || []).map((id) => state.nodes.get(id));
}

function moduleForNode(nodeId) {
  const ancestors = nodeAncestors(nodeId);
  return ancestors.length > 1 ? ancestors[1] : ancestors[0];
}

function renderModules() {
  clear(ui.moduleList);
  const currentModule = moduleForNode(state.activeNodeId);
  for (const moduleNode of topModules()) {
    const button = element(
      "button",
      {
        className: `module-button${currentModule && moduleNode.id === currentModule.id ? " active" : ""}`,
        type: "button",
        "aria-current": currentModule && moduleNode.id === currentModule.id ? "page" : null,
      },
      element("span", { text: moduleNode.name }),
      element("span", { className: "module-count", text: String(moduleNode.document_count || 0) }),
    );
    button.addEventListener("click", () => navigateToNode(moduleNode.id));
    ui.moduleList.append(button);
  }
  ui.mapToggle.classList.toggle("active", state.view === "map");
}

function renderTree() {
  clear(ui.tree);
  const root = getNode(state.data.root);
  const list = document.createDocumentFragment();
  for (const childId of root.children || []) list.append(renderTreeItem(childId));
  ui.tree.append(list);
}

function renderTreeItem(nodeId) {
  const node = getNode(nodeId);
  const childIds = state.children.get(nodeId) || [];
  const item = element("li", { className: "tree-item" });
  const row = element("div", { className: "tree-row" });
  const expanded = state.expanded.has(nodeId);
  const toggle = element("button", {
    className: `tree-toggle${childIds.length ? "" : " placeholder"}`,
    type: "button",
    "aria-label": expanded ? `折叠${node.name}` : `展开${node.name}`,
    "aria-expanded": String(expanded),
  });
  toggle.addEventListener("click", () => {
    if (state.expanded.has(nodeId)) state.expanded.delete(nodeId);
    else state.expanded.add(nodeId);
    renderTree();
  });

  const nodeButton = element(
    "button",
    {
      className: `tree-node${node.id === state.activeNodeId ? " active" : ""}`,
      type: "button",
      "aria-current": node.id === state.activeNodeId ? "page" : null,
    },
    element("span", {
      className: `node-lock${node.locked ? "" : " generated"}`,
      "aria-hidden": "true",
    }),
    element("span", { className: "node-name", text: node.name }),
    element("span", { className: "node-count", text: String(node.document_count || 0) }),
  );
  nodeButton.addEventListener("click", () => navigateToNode(nodeId));
  row.append(toggle, nodeButton);
  item.append(row);

  if (childIds.length) {
    const childList = element("ul", { className: "tree-children", hidden: !expanded });
    for (const childId of childIds) childList.append(renderTreeItem(childId));
    item.append(childList);
  }
  return item;
}

function expandAncestors(nodeId) {
  for (const ancestor of nodeAncestors(nodeId)) state.expanded.add(ancestor.id);
}

function renderBreadcrumbs(pathNodes) {
  clear(ui.breadcrumbs);
  pathNodes.forEach((node, index) => {
    if (index) ui.breadcrumbs.append(element("span", { className: "breadcrumb-separator", text: "/" }));
    const button = element("button", {
      className: "breadcrumb-button",
      type: "button",
      text: node.name,
      "aria-current": index === pathNodes.length - 1 ? "page" : null,
    });
    button.addEventListener("click", () => navigateToNode(node.id));
    ui.breadcrumbs.append(button);
  });
}

function nodeSummary(node) {
  if (node.summary) return node.summary;
  const childCount = (node.children || []).length;
  const documentCount = node.document_count || 0;
  if (node.id === state.data.root) {
    return "这里保存你的专业知识骨架。每份资料沿着严格的母子链路进入最具体的节点，跨领域联系则单独记录。";
  }
  if (childCount) {
    return `该专业节点继续分为 ${childCount} 个子方向，共汇集 ${documentCount} 篇知识笔记。`;
  }
  return documentCount
    ? `该节点已经汇集 ${documentCount} 篇知识笔记，可继续随资料增长自动扩展。`
    : "这是一个等待知识进入的专业节点；母子路径已经保留，后续资料会自动挂载在这里。";
}

function renderNodeView(nodeId) {
  state.view = "node";
  state.activeNodeId = nodeId;
  state.activeDocumentId = null;
  const node = getNode(nodeId);
  const ancestors = nodeAncestors(node.id);
  renderBreadcrumbs(ancestors);
  clear(ui.contentView);

  const directDocuments = getNodeDocuments(node.id);
  const descendantDocuments = getNodeDocuments(node.id, true);
  const childNodes = (node.children || []).map((id) => state.nodes.get(id));
  const relationCount = state.data.relations.filter(
    (relation) => relation.from_node_id === node.id || relation.to_node_id === node.id,
  ).length;

  const hero = element(
    "section",
    { className: "hero" },
    element(
      "div",
      { className: "hero-topline" },
      element("span", {
        className: "level-label",
        text: node.id === state.data.root ? "知识总览" : `第 ${node.level} 级专业`,
      }),
      node.locked ? element("span", { className: "status-chip", text: "稳定骨架" }) : null,
    ),
    element("h1", { text: node.name }),
    element("p", { className: "hero-copy", text: nodeSummary(node) }),
    element(
      "ul",
      { className: "hero-stats", "aria-label": "节点统计" },
      element("li", {}, element("strong", { text: String(childNodes.length) }), element("span", { text: "直接子节点" })),
      element("li", {}, element("strong", { text: String(descendantDocuments.length) }), element("span", { text: "知识笔记" })),
      element("li", {}, element("strong", { text: String(relationCount) }), element("span", { text: "辅助关系" })),
    ),
  );
  ui.contentView.append(hero);

  if (childNodes.length) {
    const childSection = element(
      "section",
      { className: "section-block" },
      element(
        "div",
        { className: "section-title" },
        element("h2", { text: "继续深入" }),
        element("span", { text: `${childNodes.length} 个专业方向` }),
      ),
    );
    const grid = element("div", { className: "child-grid" });
    for (const child of childNodes) {
      const card = element(
        "button",
        { className: "child-card", type: "button" },
        element("small", { text: `第 ${child.level} 级 · ${child.document_count || 0} 篇` }),
        element("strong", { text: child.name }),
        element("p", { text: nodeSummary(child) }),
      );
      card.addEventListener("click", () => navigateToNode(child.id));
      grid.append(card);
    }
    childSection.append(grid);
    ui.contentView.append(childSection);
  }

  const documentSection = element(
    "section",
    { className: "section-block" },
    element(
      "div",
      { className: "section-title" },
      element("h2", { text: "当前节点的知识" }),
      element("span", { text: `${directDocuments.length} 篇` }),
    ),
  );
  if (!directDocuments.length) {
    documentSection.append(
      element(
        "div",
        { className: "empty-state" },
        element("strong", { text: "这个节点还没有直接挂载的资料" }),
        element("span", {
          text: childNodes.length ? "可以继续进入下级专业方向。" : "新资料会自动进入最准确的末级节点。",
        }),
      ),
    );
  } else {
    const list = element("div", { className: "document-list" });
    directDocuments.forEach((documentItem, index) => list.append(renderDocumentCard(documentItem, index)));
    documentSection.append(list);
  }
  ui.contentView.append(documentSection);
  renderNodeContext(node);
  renderModules();
  renderTree();
}

function renderDocumentCard(documentItem, index) {
  const card = element(
    "button",
    {
      className: `document-card${state.activeDocumentId === documentItem.id ? " active" : ""}`,
      type: "button",
    },
    element("span", { className: "document-index", text: String(index + 1).padStart(2, "0") }),
    element(
      "span",
      {},
      element("strong", { text: documentItem.title }),
      element("p", { text: documentItem.summary || "暂无摘要" }),
      element(
        "span",
        { className: "document-meta" },
        element("span", { text: statusLabel(documentItem.status) }),
        element("time", { text: formatDate(documentItem.updated_at) }),
      ),
    ),
    element("span", { className: "document-arrow", text: "›", "aria-hidden": "true" }),
  );
  card.addEventListener("click", () => navigateToDocument(documentItem.id));
  return card;
}

function renderDocumentView(documentId) {
  const documentItem = state.documents.get(documentId);
  if (!documentItem) {
    navigateToNode(state.data.root);
    return;
  }
  state.view = "document";
  state.activeDocumentId = documentItem.id;
  state.activeNodeId = documentItem.node_id;
  const pathNodes = nodeAncestors(documentItem.node_id);
  renderBreadcrumbs(pathNodes);
  clear(ui.contentView);

  const topLine = element(
    "div",
    { className: "hero-topline" },
    element("span", { className: "level-label", text: "知识笔记" }),
    element("span", { className: "status-chip", text: statusLabel(documentItem.status) }),
  );
  const view = element(
    "article",
    { className: "document-view" },
    topLine,
    element("h1", { text: documentItem.title }),
    element("p", {
      className: "document-lede",
      text: documentItem.summary || "这篇知识尚未生成摘要。",
    }),
  );
  if (documentItem.tags.length) {
    view.append(
      element(
        "div",
        { className: "tag-row", "aria-label": "标签" },
        documentItem.tags.map((tag) => element("span", { className: "tag", text: tag })),
      ),
    );
  }
  if (documentItem.key_points.length) {
    view.append(
      element(
        "section",
        { className: "section-block" },
        element("div", { className: "section-title" }, element("h2", { text: "关键知识" })),
        element(
          "ul",
          { className: "key-points" },
          documentItem.key_points.map((point) => element("li", { text: point })),
        ),
      ),
    );
  }
  if (documentItem.content) {
    view.append(
      element(
        "section",
        { className: "section-block" },
        element("div", { className: "section-title" }, element("h2", { text: "知识正文" })),
        element("div", { className: "knowledge-body", text: documentItem.content }),
      ),
    );
  }
  ui.contentView.append(view);
  renderDocumentContext(documentItem);
  expandAncestors(documentItem.node_id);
  renderModules();
  renderTree();
}

function contextSection(title, count) {
  return element(
    "section",
    { className: "context-section" },
    element("h3", {}, element("span", { text: title }), element("span", { text: String(count) })),
  );
}

function sourceCard(documentItem) {
  const source = documentItem.source || {};
  if (!Object.keys(source).length) return null;
  const title = source.original_name || source.origin || "来源记录";
  const description = element(
    "dl",
    {},
    element("dt", { text: "类型" }),
    element("dd", { text: source.kind || "unknown" }),
  );
  if (source.sha256) {
    description.append(element("dt", { text: "指纹" }), element("dd", { text: source.sha256.slice(0, 16) }));
  }
  if (source.available_locally) {
    description.append(element("dt", { text: "原件" }), element("dd", { text: "仅本机保存" }));
  }
  const card = element("article", { className: "source-card" }, element("strong", { text: title }), description);
  const href = safeHttpUrl(source.origin);
  if (href) {
    const link = element("a", {
      className: "source-link",
      href,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "打开原始来源 ↗",
    });
    card.append(link);
  }
  return card;
}

function evidenceCard(evidence) {
  const footer = [evidence.source_label, evidence.locator].filter(Boolean).join(" · ");
  return element(
    "article",
    { className: "evidence-card" },
    element("blockquote", { text: `“${evidence.excerpt}”` }),
    footer ? element("footer", { text: footer }) : null,
  );
}

function relationsForNode(nodeId, documentId = null) {
  return state.data.relations.filter((relation) => {
    const touchesNode = relation.from_node_id === nodeId || relation.to_node_id === nodeId;
    if (!touchesNode) return false;
    if (!documentId) return true;
    return !relation.document_id || relation.document_id === documentId;
  });
}

function relationCard(relation, currentNodeId) {
  const otherId = relation.from_node_id === currentNodeId ? relation.to_node_id : relation.from_node_id;
  const other = getNode(otherId);
  const card = element(
    "button",
    { className: "relation-card", type: "button" },
    element(
      "span",
      {},
      element("strong", { text: other.name }),
      element("small", { text: pathText(other.path) }),
    ),
    element("span", { className: "relation-type", text: relationLabel(relation) }),
  );
  card.addEventListener("click", () => navigateToNode(other.id));
  return card;
}

function renderDocumentContext(documentItem) {
  clear(ui.contextView);
  const source = sourceCard(documentItem);
  if (source) {
    const section = contextSection("原始来源", 1);
    section.append(source);
    ui.contextView.append(section);
  }

  const evidence = documentItem.evidence || [];
  if (evidence.length) {
    const section = contextSection("证据片段", evidence.length);
    for (const item of evidence) section.append(evidenceCard(item));
    ui.contextView.append(section);
  }

  const relations = relationsForNode(documentItem.node_id, documentItem.id);
  if (relations.length) {
    const section = contextSection("辅助关系", relations.length);
    for (const relation of relations) section.append(relationCard(relation, documentItem.node_id));
    ui.contextView.append(section);
  }

  if (!source && !evidence.length && !relations.length) {
    ui.contextView.append(
      element(
        "div",
        { className: "empty-context" },
        element("span", { text: "◎", "aria-hidden": "true" }),
        element("p", { text: "这篇知识尚未附加来源、证据或辅助关系。" }),
      ),
    );
  }
}

function renderNodeContext(node) {
  clear(ui.contextView);
  const relations = relationsForNode(node.id);
  if (relations.length) {
    const section = contextSection("节点辅助关系", relations.length);
    for (const relation of relations) section.append(relationCard(relation, node.id));
    ui.contextView.append(section);
  }

  const recentDocuments = getNodeDocuments(node.id, true).slice(0, 4);
  if (recentDocuments.length) {
    const section = contextSection("最近知识", recentDocuments.length);
    for (const documentItem of recentDocuments) {
      const card = element(
        "button",
        { className: "relation-card", type: "button" },
        element(
          "span",
          {},
          element("strong", { text: documentItem.title }),
          element("small", { text: formatDate(documentItem.updated_at) }),
        ),
        element("span", { className: "relation-type", text: statusLabel(documentItem.status) }),
      );
      card.addEventListener("click", () => navigateToDocument(documentItem.id));
      section.append(card);
    }
    ui.contextView.append(section);
  }

  if (!relations.length && !recentDocuments.length) {
    ui.contextView.append(
      element(
        "div",
        { className: "empty-context" },
        element("span", { text: "◎", "aria-hidden": "true" }),
        element("p", { text: "这个专业节点还没有来源证据或跨领域关联。" }),
      ),
    );
  }
}

function setHash(parameters) {
  const query = new URLSearchParams(parameters);
  const nextHash = query.toString();
  if (window.location.hash.slice(1) === nextHash) return;
  history.pushState(null, "", `#${nextHash}`);
}

function navigateToNode(nodeId, updateHash = true) {
  const node = state.nodes.get(nodeId) || state.nodes.get(state.data.root);
  expandAncestors(node.id);
  renderNodeView(node.id);
  if (updateHash) setHash({ node: node.id });
  ui.contentPanel.scrollTo({ top: 0, behavior: "smooth" });
  setMobilePanel("content");
}

function navigateToDocument(documentId, updateHash = true) {
  const documentItem = state.documents.get(documentId);
  if (!documentItem) return;
  renderDocumentView(documentId);
  if (updateHash) setHash({ document: documentId });
  ui.contentPanel.scrollTo({ top: 0, behavior: "smooth" });
  if (window.innerWidth > 680 && window.innerWidth <= 1180) document.body.classList.add("context-open");
}

function navigateToMap(updateHash = true) {
  state.view = "map";
  state.activeDocumentId = null;
  renderMapView();
  renderModules();
  if (updateHash) setHash({ map: state.activeNodeId || state.data.root });
  setMobilePanel("content");
}

function readRoute() {
  const parameters = new URLSearchParams(window.location.hash.slice(1));
  if (parameters.has("document")) {
    navigateToDocument(parameters.get("document"), false);
  } else if (parameters.has("map")) {
    const nodeId = parameters.get("map");
    if (state.nodes.has(nodeId)) state.activeNodeId = nodeId;
    navigateToMap(false);
  } else {
    navigateToNode(parameters.get("node") || state.data.root, false);
  }
}

function showSearch() {
  window.dispatchEvent(new Event("knowledge:close-ingest"));
  ui.searchDialog.hidden = false;
  document.body.classList.add("search-open");
  window.setTimeout(() => ui.searchInput.focus(), 0);
}

function hideSearch() {
  ui.searchDialog.hidden = true;
  document.body.classList.remove("search-open");
  ui.searchInput.value = "";
  clear(ui.searchResults);
  ui.searchHint.textContent = "输入关键词开始搜索；支持中文连续匹配。";
  ui.searchTrigger.focus();
}

function tokenizeQuery(value) {
  const normalized = value.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return [];
  const spaced = normalized.split(/\s+/).filter(Boolean);
  return [...new Set([normalized, ...spaced])];
}

function scoreSearchItem(item, tokens) {
  const title = String(item.title || "").toLocaleLowerCase("zh-CN");
  const path = pathText(item.path).toLocaleLowerCase("zh-CN");
  const tags = (item.tags || []).join(" ").toLocaleLowerCase("zh-CN");
  const summary = String(item.summary || "").toLocaleLowerCase("zh-CN");
  const body = String(item.search_text || "").toLocaleLowerCase("zh-CN");
  let score = 0;
  for (const token of tokens) {
    if (!body.includes(token)) return 0;
    if (title === token) score += 30;
    else if (title.includes(token)) score += 12;
    if (path.includes(token)) score += 7;
    if (tags.includes(token)) score += 5;
    if (summary.includes(token)) score += 3;
    score += Math.max(1, 4 - body.indexOf(token) / 500);
  }
  return score;
}

function runSearch(value) {
  const tokens = tokenizeQuery(value);
  clear(ui.searchResults);
  if (!tokens.length) {
    ui.searchHint.textContent = "输入关键词开始搜索；支持中文连续匹配。";
    return;
  }
  const results = state.search.items
    .map((item) => ({ item, score: scoreSearchItem(item, tokens) }))
    .filter((result) => result.score > 0)
    .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title, "zh-CN"))
    .slice(0, 30);
  ui.searchHint.textContent = results.length ? `找到 ${results.length} 条最相关结果` : "没有找到匹配内容";
  for (const result of results) {
    const item = result.item;
    const button = element(
      "button",
      { className: "search-result", type: "button" },
      element("span", { className: "result-kind", text: item.type === "document" ? "文" : "类" }),
      element(
        "span",
        {},
        element("strong", { text: item.title }),
        element("small", { text: pathText(item.path) }),
      ),
      element("span", { text: "›", "aria-hidden": "true" }),
    );
    button.addEventListener("click", () => {
      hideSearch();
      if (item.type === "document") navigateToDocument(item.id);
      else navigateToNode(item.node_id);
    });
    ui.searchResults.append(element("li", {}, button));
  }
}

function visibleGraphNodes() {
  const anchorId = state.activeNodeId || state.data.root;
  let nodes = state.graph.nodes.filter((node) => isDescendant(node.id, anchorId));
  if (nodes.length < 2 && anchorId !== state.data.root) {
    const moduleNode = moduleForNode(anchorId);
    nodes = state.graph.nodes.filter((node) => isDescendant(node.id, moduleNode.id));
  }
  return nodes.slice(0, 120);
}

function graphLayout(nodes, width, height) {
  const groups = new Map();
  const minLevel = Math.min(...nodes.map((node) => node.level));
  const maxLevel = Math.max(...nodes.map((node) => node.level));
  for (const node of nodes) {
    if (!groups.has(node.level)) groups.set(node.level, []);
    groups.get(node.level).push(node);
  }
  const positions = new Map();
  for (const [level, group] of groups) {
    group.sort((left, right) => pathText(left.path).localeCompare(pathText(right.path), "zh-CN"));
    const y = 60 + ((level - minLevel) / Math.max(1, maxLevel - minLevel)) * (height - 120);
    group.forEach((node, index) => {
      const x = ((index + 1) / (group.length + 1)) * (width - 80) + 40;
      positions.set(node.id, { x, y });
    });
  }
  return positions;
}

function drawGraph() {
  const canvas = document.getElementById("graph-canvas");
  if (!canvas || !state.graph) return;
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(320, bounds.width);
  const height = Math.max(420, bounds.height);
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);

  const nodes = visibleGraphNodes();
  if (!nodes.length) return;
  const ids = new Set(nodes.map((node) => node.id));
  const positions = graphLayout(nodes, width, height);
  state.graphHitboxes = [];

  context.lineWidth = 1;
  for (const edge of state.graph.tree_edges) {
    if (!ids.has(edge.from) || !ids.has(edge.to)) continue;
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.strokeStyle = "rgba(79, 93, 112, 0.22)";
    context.stroke();
  }
  for (const edge of state.graph.relation_edges) {
    if (!ids.has(edge.from) || !ids.has(edge.to)) continue;
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    const middleX = (from.x + to.x) / 2;
    const bend = Math.min(55, Math.abs(from.x - to.x) * 0.2 + 18);
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.quadraticCurveTo(middleX, Math.min(from.y, to.y) - bend, to.x, to.y);
    context.strokeStyle = "rgba(213, 155, 74, 0.82)";
    context.lineWidth = 2;
    context.setLineDash([5, 4]);
    context.stroke();
    context.setLineDash([]);
  }

  for (const node of nodes) {
    const position = positions.get(node.id);
    const active = node.id === state.activeNodeId;
    const radius = active ? 9 : 6;
    context.beginPath();
    context.arc(position.x, position.y, radius + 4, 0, Math.PI * 2);
    context.fillStyle = active ? "rgba(67, 133, 127, 0.14)" : "rgba(255, 254, 251, 0.9)";
    context.fill();
    context.beginPath();
    context.arc(position.x, position.y, radius, 0, Math.PI * 2);
    context.fillStyle = active ? "#43857f" : node.level <= 1 ? "#d59b4a" : "#718398";
    context.fill();

    context.font = `${active ? "600 " : ""}11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "top";
    context.fillStyle = active ? "#172236" : "#4f5d70";
    const label = node.name.length > 12 ? `${node.name.slice(0, 11)}…` : node.name;
    context.fillText(label, position.x, position.y + radius + 7);
    state.graphHitboxes.push({ id: node.id, x: position.x, y: position.y, radius: 22 });
  }
}

function renderMapView() {
  renderBreadcrumbs(nodeAncestors(state.activeNodeId || state.data.root));
  clear(ui.contentView);
  const node = getNode(state.activeNodeId || state.data.root);
  const view = element(
    "section",
    { className: "map-view" },
    element(
      "div",
      { className: "map-head" },
      element(
        "div",
        {},
        element("span", { className: "eyebrow", text: "AUXILIARY RELATION MAP" }),
        element("h1", { text: `${node.name} · 知识地图` }),
        element("p", {
          text: "灰线表达严格母子结构；金色虚线表达跨节点辅助关系。关系不会改变专业目录。",
        }),
      ),
      element(
        "div",
        { className: "map-legend" },
        element("span", {}, element("i"), "母子链路"),
        element("span", {}, element("i", { className: "relation" }), "辅助关系"),
      ),
    ),
  );
  const shell = element(
    "div",
    { className: "graph-shell" },
    element("canvas", {
      className: "graph-canvas",
      id: "graph-canvas",
      role: "img",
      "aria-label": `${node.name}专业母子树与辅助关系图`,
    }),
    element("span", { className: "graph-note", text: "点击节点进入对应知识目录" }),
  );
  view.append(shell);
  ui.contentView.append(view);
  const canvas = shell.querySelector("canvas");
  canvas.addEventListener("click", (event) => {
    const bounds = canvas.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const hit = state.graphHitboxes.find((item) => Math.hypot(item.x - x, item.y - y) <= item.radius);
    if (hit) navigateToNode(hit.id);
  });
  requestAnimationFrame(drawGraph);
}

function setMobilePanel(panel) {
  document.body.dataset.mobilePanel = panel;
  for (const button of ui.mobileTabs) {
    button.setAttribute("aria-pressed", String(button.dataset.panel === panel));
  }
}

function updateConnection() {
  const online = navigator.onLine;
  ui.connectionDot.classList.toggle("offline", !online);
  ui.connectionDot.title = online ? "已连接" : "离线";
  if (!online) showToast("当前处于离线状态");
}

let toastTimer;
function showToast(message) {
  window.clearTimeout(toastTimer);
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    ui.toast.hidden = true;
  }, 2600);
}

function bindUi() {
  ui.app = document.getElementById("app");
  ui.siteTitle = document.getElementById("site-title");
  ui.updatedAt = document.getElementById("updated-at");
  ui.privacyBadge = document.getElementById("privacy-badge");
  ui.connectionDot = document.getElementById("connection-dot");
  ui.moduleList = document.getElementById("module-list");
  ui.mapToggle = document.getElementById("map-toggle");
  ui.tree = document.getElementById("knowledge-tree");
  ui.collapseTree = document.getElementById("collapse-tree");
  ui.contentPanel = document.getElementById("knowledge-content");
  ui.contentView = document.getElementById("content-view");
  ui.breadcrumbs = document.getElementById("breadcrumbs");
  ui.contextView = document.getElementById("context-view");
  ui.closeContext = document.getElementById("close-context");
  ui.searchTrigger = document.getElementById("search-trigger");
  ui.searchDialog = document.getElementById("search-dialog");
  ui.searchInput = document.getElementById("search-input");
  ui.searchHint = document.getElementById("search-hint");
  ui.searchResults = document.getElementById("search-results");
  ui.mobileTabs = [...document.querySelectorAll(".mobile-tabs button")];
  ui.toast = document.getElementById("toast");

  ui.mapToggle.addEventListener("click", () => {
    if (state.view === "map") navigateToNode(state.activeNodeId || state.data.root);
    else navigateToMap();
  });
  ui.collapseTree.addEventListener("click", () => {
    state.expanded.clear();
    renderTree();
  });
  ui.closeContext.addEventListener("click", () => document.body.classList.remove("context-open"));
  ui.searchTrigger.addEventListener("click", showSearch);
  for (const closer of document.querySelectorAll("[data-close-search]")) {
    closer.addEventListener("click", hideSearch);
  }
  ui.searchInput.addEventListener("input", (event) => runSearch(event.target.value));
  for (const button of ui.mobileTabs) {
    button.addEventListener("click", () => setMobilePanel(button.dataset.panel));
  }
  window.addEventListener("hashchange", readRoute);
  window.addEventListener("online", updateConnection);
  window.addEventListener("offline", updateConnection);
  window.addEventListener("knowledge:close-search", () => {
    if (!ui.searchDialog.hidden) hideSearch();
  });
  window.addEventListener("resize", () => {
    if (state.view === "map") drawGraph();
    if (window.innerWidth > 1180) document.body.classList.remove("context-open");
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      showSearch();
    }
    if (event.key === "Escape") {
      if (!ui.searchDialog.hidden) hideSearch();
      else document.body.classList.remove("context-open");
    }
  });
}

function showFatalError(error) {
  clear(ui.contentView).append(
    element(
      "section",
      { className: "error-state" },
      element("h1", { text: "知识网站暂时无法载入" }),
      element("p", { text: "构建数据可能缺失或格式不正确。线上旧版本不会因此被修改。" }),
      element("code", { text: error instanceof Error ? error.message : String(error) }),
    ),
  );
  ui.app.setAttribute("aria-busy", "false");
}

async function start() {
  bindUi();
  if (window.KnowledgeLocalIngest) {
    void window.KnowledgeLocalIngest.mount({ notify: showToast }).catch(() => {
      // A static/public build intentionally has no writable local manager.
    });
  }
  try {
    const dataSource = window.KnowledgeDataSources.fromDocument(document);
    const { data, search, graph } = await dataSource.loadWorkspace();
    state.data = data;
    state.search = search;
    state.graph = graph;
    setupIndexes();
    state.activeNodeId = data.root;
    state.expanded.add(data.root);
    ui.siteTitle.textContent = data.site.title;
    ui.updatedAt.textContent = formatDate(data.generated_at);
    ui.updatedAt.dateTime = data.generated_at || "";
    const privateBuild = data.site.visibility === "private";
    ui.privacyBadge.textContent = privateBuild ? "私密知识库" : "公开知识库";
    document.documentElement.dataset.visibility = data.site.visibility;
    updateConnection();
    const pendingDocumentId = window.KnowledgeLocalIngest?.peekPendingDocumentId();
    if (pendingDocumentId && state.documents.has(pendingDocumentId)) {
      navigateToDocument(pendingDocumentId);
      if (state.activeDocumentId === pendingDocumentId) {
        window.KnowledgeLocalIngest.clearPendingDocumentId();
        showToast("已打开刚刚整理完成的知识");
      }
    } else {
      readRoute();
    }
    ui.app.setAttribute("aria-busy", "false");

    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("./service-worker.js").catch(() => {
        showToast("离线应用注册失败，不影响在线浏览");
      });
    }
  } catch (error) {
    showFatalError(error);
  }
}

start();
