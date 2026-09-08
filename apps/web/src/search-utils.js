"use strict";

(function exposeKnowledgeSearch(global) {
  const FILTERS = Object.freeze([
    { value: "all", label: "全部" },
    { value: "document", label: "知识卡" },
    { value: "node", label: "专业节点" },
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
    return [...new Set([normalized, ...spaced])];
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

  function search(items, query, { filter = "all", limit = 30 } = {}) {
    const tokens = tokenizeQuery(query);
    if (!tokens.length) return [];
    const maximum = Number.isSafeInteger(limit) ? Math.max(0, Math.min(100, limit)) : 30;
    return (Array.isArray(items) ? items : [])
      .filter((item) => filter === "all" || item?.type === filter)
      .map((item) => ({ item, score: scoreSearchItem(item, tokens) }))
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
    filterLabel,
    tokenizeQuery,
    scoreSearchItem,
    search,
  });
})(typeof window !== "undefined" ? window : globalThis);
