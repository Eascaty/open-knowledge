"use strict";

(function exposeKnowledgeSearch(global) {
  const FILTERS = Object.freeze([
    { value: "all", label: "全部" },
    { value: "document", label: "知识卡" },
    { value: "node", label: "专业节点" },
  ]);
  const REVIEW_STATUSES = Object.freeze([
    { value: "unverified", label: "待验证" },
    { value: "personal", label: "个人认知" },
    { value: "supported", label: "有证据支持" },
    { value: "verified-by-practice", label: "实践验证" },
    { value: "contradicted", label: "存在争议" },
    { value: "deprecated", label: "已过时" },
  ]);

  function filterLabel(value = "all") {
    return FILTERS.find((item) => item.value === value)?.label || "全部";
  }

  function pathText(path) {
    return Array.isArray(path) ? path.join(" / ") : "";
  }

  function tokenizeQuery(value) {
    const normalized = String(value || "").trim().toLocaleLowerCase("zh-CN");
    if (!normalized) return [];
    const spaced = normalized.split(/\s+/).filter(Boolean);
    return [...new Set(spaced)];
  }

  function scoreSearchItem(item, tokens) {
    const title = String(item?.title || "").toLocaleLowerCase("zh-CN");
    const path = pathText(item?.path).toLocaleLowerCase("zh-CN");
    const tags = (Array.isArray(item?.tags) ? item.tags : []).join(" ").toLocaleLowerCase("zh-CN");
    const summary = String(item?.summary || "").toLocaleLowerCase("zh-CN");
    const body = String(item?.search_text || "").toLocaleLowerCase("zh-CN");
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

  function facetOptions(items) {
    const paths = new Map();
    const tags = new Set();
    const statusCounts = new Map();
    for (const item of items || []) {
      const path = Array.isArray(item.path) ? item.path : [];
      for (let length = 1; length <= path.length; length += 1) {
        const prefix = path.slice(0, length);
        paths.set(JSON.stringify(prefix), prefix);
      }
      for (const tag of item.tags || []) tags.add(tag);
      if (item?.type === "document") {
        const status = String(item.status || "unverified");
        statusCounts.set(status, (statusCounts.get(status) || 0) + 1);
      }
    }
    return {
      paths: [...paths.values()].sort((a, b) => pathText(a).localeCompare(pathText(b), "zh-CN")),
      tags: [...tags].sort((a, b) => a.localeCompare(b, "zh-CN")),
      statuses: REVIEW_STATUSES
        .filter((item) => statusCounts.has(item.value))
        .map((item) => ({ ...item, count: statusCounts.get(item.value) })),
    };
  }

  function search(
    items,
    query,
    { filter = "all", limit = 30, path = [], tag = "", status = "" } = {},
  ) {
    const tokens = tokenizeQuery(query);
    if (!tokens.length && !path.length && !tag && !status) return [];
    const maximum = Number.isSafeInteger(limit) ? Math.max(0, Math.min(100, limit)) : 30;
    return (Array.isArray(items) ? items : [])
      .filter((item) => filter === "all" || item?.type === filter)
      .filter((item) => path.every((part, index) => item?.path?.[index] === part))
      .filter((item) => !tag || (item?.type === "document" && item.tags?.includes(tag)))
      .filter((item) => !status || (
        item?.type === "document" && String(item.status || "unverified") === status
      ))
      .map((item) => ({ item, score: tokens.length ? scoreSearchItem(item, tokens) : 1 }))
      .filter((result) => result.score > 0)
      .sort((left, right) => (
        right.score - left.score
        || String(left.item.title || "").localeCompare(String(right.item.title || ""), "zh-CN")
        || String(left.item.id || "").localeCompare(String(right.item.id || ""))
      ))
      .slice(0, maximum);
  }

  global.KnowledgeSearch = Object.freeze({
    FILTERS,
    REVIEW_STATUSES,
    filterLabel,
    tokenizeQuery,
    scoreSearchItem,
    facetOptions,
    search,
  });
})(typeof window !== "undefined" ? window : globalThis);
