"use strict";

(function exposeReadingHistory(global) {
  const STORAGE_KEY = "knowledge-os:recent-documents:v1";
  const DEFAULT_LIMIT = 8;
  const MAX_LIMIT = 8;

  function boundedLimit(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_LIMIT;
    return Math.min(MAX_LIMIT, Math.max(1, Math.floor(numeric)));
  }

  function storageFrom(explicit) {
    if (explicit !== undefined) return explicit;
    try { return global.localStorage; } catch { return null; }
  }

  function validId(value) {
    return typeof value === "string" && value.trim().length > 0 && value.length <= 200;
  }

  function read({ storage, limit = DEFAULT_LIMIT } = {}) {
    const source = storageFrom(storage);
    const bounded = boundedLimit(limit);
    if (!source) return [];
    let parsed;
    try { parsed = JSON.parse(source.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
    if (!Array.isArray(parsed)) return [];
    const unique = new Map();
    for (const item of parsed) {
      if (!item || !validId(item.id)) continue;
      const viewedAt = Number(item.viewedAt);
      if (!Number.isFinite(viewedAt) || viewedAt < 0) continue;
      const current = unique.get(item.id);
      if (!current || viewedAt > current.viewedAt) unique.set(item.id, { id: item.id, viewedAt });
    }
    return [...unique.values()]
      .sort((left, right) => right.viewedAt - left.viewedAt || left.id.localeCompare(right.id))
      .slice(0, bounded);
  }

  function record(id, { storage, now = Date.now(), limit = DEFAULT_LIMIT } = {}) {
    if (!validId(id)) return false;
    const source = storageFrom(storage);
    if (!source) return false;
    const viewedAt = Number(now);
    if (!Number.isFinite(viewedAt) || viewedAt < 0) return false;
    try {
      const bounded = boundedLimit(limit);
      const next = read({ storage: source, limit: bounded })
        .filter((item) => item.id !== id);
      next.unshift({ id, viewedAt });
      source.setItem(STORAGE_KEY, JSON.stringify(next.slice(0, bounded)));
      return true;
    } catch { return false; }
  }

  function clear({ storage } = {}) {
    const source = storageFrom(storage);
    if (!source) return false;
    try { source.removeItem(STORAGE_KEY); return true; } catch { return false; }
  }

  function recentEntries(documents, options = {}) {
    const lookup = documents instanceof Map
      ? documents
      : new Map((Array.isArray(documents) ? documents : []).filter((item) => item && validId(item.id)).map((item) => [item.id, item]));
    return read(options)
      .map((entry) => ({ ...entry, document: lookup.get(entry.id) }))
      .filter((entry) => entry.document);
  }

  global.KnowledgeReadingHistory = Object.freeze({
    key: STORAGE_KEY,
    maxLimit: MAX_LIMIT,
    read,
    record,
    clear,
    recentEntries,
  });

  const FAVORITE_LIMIT = 200;
  function createFavorites({ storage, scope } = {}) {
    if (typeof scope !== "string" || !scope || scope.length > 2000) throw new Error("无效的收藏范围");
    const key = `knowledge-os:favorites:v1:${scope}`;
    function snapshot() {
      try {
        const source = storageFrom(storage);
        if (!source) throw new Error("浏览器存储不可用");
        const raw = source.getItem(key);
        if (raw === null) return { items: [], error: "" };
        if (raw.length > 200000) throw new Error("收藏记录过大");
        const data = JSON.parse(raw);
        if (data?.version !== 1 || !Array.isArray(data.items) || data.items.length > FAVORITE_LIMIT) throw new Error("收藏格式不受支持");
        const unique = new Map();
        for (const item of data.items) {
          if (!validId(item?.id) || !Number.isSafeInteger(item.savedAt) || item.savedAt < 0) throw new Error("收藏记录损坏");
          const previous = unique.get(item.id);
          if (!previous || previous.savedAt < item.savedAt) unique.set(item.id, { id: item.id, savedAt: item.savedAt });
        }
        return { items: [...unique.values()].sort((a, b) => b.savedAt - a.savedAt || a.id.localeCompare(b.id)), error: "" };
      } catch { return { items: [], error: "收藏暂不可用，浏览器存储受限或记录损坏；已有记录不会被覆盖" }; }
    }
    function set(id, selected, now = Date.now()) {
      const current = snapshot();
      if (current.error) return { ok: false, message: current.error };
      if (!validId(id) || typeof selected !== "boolean" || !Number.isSafeInteger(now) || now < 0) return { ok: false, message: "无效的收藏记录" };
      const existing = current.items.find(item => item.id === id);
      const items = current.items.filter(item => item.id !== id);
      if (selected) {
        if (items.length >= FAVORITE_LIMIT) return { ok: false, message: "最多收藏200张卡片，请先取消部分收藏" };
        items.push(existing || { id, savedAt: now });
      }
      try {
        storageFrom(storage).setItem(key, JSON.stringify({ version: 1, items }));
        return { ok: true, selected, message: selected ? "已收藏，仅保存在此浏览器" : "已取消收藏" };
      } catch { return { ok: false, message: "收藏未保存，浏览器存储不可用或空间不足" }; }
    }
    return Object.freeze({ key, snapshot, set,
      has: id => snapshot().items.some(item => item.id === id),
      exportData() {
        const current = snapshot();
        if (current.error) throw new Error(current.error);
        return { format: "knowledge-favorites", version: 1, items: current.items };
      },
    });
  }
  global.KnowledgeFavorites = Object.freeze({ createStore: createFavorites, maxLimit: FAVORITE_LIMIT });
})(typeof window !== "undefined" ? window : globalThis);
