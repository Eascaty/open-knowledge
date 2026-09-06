"use strict";

(function exposeReadingHistory(global) {
  const STORAGE_KEY = "knowledge-os:recent-documents:v1";
  const DEFAULT_LIMIT = 8;

  function storageFrom(explicit) {
    if (explicit !== undefined) return explicit;
    try { return global.localStorage; } catch { return null; }
  }

  function validId(value) {
    return typeof value === "string" && value.trim().length > 0 && value.length <= 200;
  }

  function read({ storage, limit = DEFAULT_LIMIT } = {}) {
    const source = storageFrom(storage);
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
      .slice(0, Math.max(1, Number(limit) || DEFAULT_LIMIT));
  }

  function record(id, { storage, now = Date.now(), limit = DEFAULT_LIMIT } = {}) {
    if (!validId(id)) return false;
    const source = storageFrom(storage);
    if (!source) return false;
    const viewedAt = Number(now);
    if (!Number.isFinite(viewedAt) || viewedAt < 0) return false;
    try {
      const next = read({ storage: source, limit: Math.max(DEFAULT_LIMIT, Number(limit) || DEFAULT_LIMIT) })
        .filter((item) => item.id !== id);
      next.unshift({ id, viewedAt });
      source.setItem(STORAGE_KEY, JSON.stringify(next.slice(0, Math.max(1, Number(limit) || DEFAULT_LIMIT))));
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
    read,
    record,
    clear,
    recentEntries,
  });
})(typeof window !== "undefined" ? window : globalThis);
