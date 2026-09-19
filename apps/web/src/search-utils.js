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

  function highlightParts(text, query) {
    const value = String(text || "");
    const tokens = tokenizeQuery(query).sort((a, b) => b.length - a.length);
    if (!tokens.length) return [{ text: value, matched: false }];
    const escaped = tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const expression = new RegExp(escaped.join("|"), "giu");
    const parts = [];
    let end = 0;
    for (const match of value.matchAll(expression)) {
      if (match.index > end) parts.push({ text: value.slice(end, match.index), matched: false });
      parts.push({ text: match[0], matched: true });
      end = match.index + match[0].length;
    }
    if (end < value.length) parts.push({ text: value.slice(end), matched: false });
    return parts;
  }

  function resultSnippet(item, query, content = "") {
    const tokens = tokenizeQuery(query);
    const candidates = [content, item?.summary, item?.search_text]
      .filter(Boolean).map((text) => String(text).replace(/\s+/g, " ").trim());
    const position = (text) => {
      const normalized = text.toLocaleLowerCase("zh-CN");
      const matches = tokens.map((token) => normalized.indexOf(token)).filter((index) => index >= 0);
      return matches.length ? Math.min(...matches) : -1;
    };
    const text = candidates.find((candidate) => position(candidate) >= 0) || candidates[0] || "";
    const start = Math.max(0, position(text) - 40);
    const end = Math.min(text.length, start + 180);
    return `${start ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
  }

  function search(
    items,
    query,
    { filter = "all", limit = 30, offset = 0, path = [], tag = "", status = "" } = {},
  ) {
    const tokens = tokenizeQuery(query);
    if (!tokens.length && !path.length && !tag && !status) return [];
    const maximum = Number.isSafeInteger(limit) ? Math.max(0, Math.min(100, limit)) : 30;
    const start = Number.isSafeInteger(offset) ? Math.max(0, offset) : 0;
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
      .slice(start, start + maximum);
  }

  global.KnowledgeSearch = Object.freeze({
    FILTERS,
    REVIEW_STATUSES,
    filterLabel,
    tokenizeQuery,
    scoreSearchItem,
    facetOptions,
    highlightParts,
    resultSnippet,
    search,
  });
})(typeof window !== "undefined" ? window : globalThis);
