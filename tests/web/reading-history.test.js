"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) || null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

const context = {};
vm.runInNewContext(fs.readFileSync("apps/web/src/reading-history.js", "utf8"), context);
const api = context.KnowledgeReadingHistory;

function testRecordsNewestFirstAndCapsEntries() {
  const storage = new Storage();
  assert.equal(api.record("java", { storage, now: 10, limit: 2 }), true);
  assert.equal(api.record("finance", { storage, now: 20, limit: 2 }), true);
  assert.equal(api.record("ai", { storage, now: 30, limit: 2 }), true);
  assert.equal(Array.from(api.read({ storage, limit: 2 }), (item) => item.id).join(","), "ai,finance");
  api.record("finance", { storage, now: 40, limit: 2 });
  assert.equal(Array.from(api.read({ storage, limit: 2 }), (item) => item.id).join(","), "finance,ai");
}

function testMalformedStorageAndDocumentLookupAreSafe() {
  const storage = new Storage();
  storage.setItem(api.key, JSON.stringify([{ id: "ok", viewedAt: 2 }, { id: "bad", viewedAt: "no" }, null]));
  const entries = api.recentEntries([
    { id: "missing", title: "不应显示" },
    { id: "ok", title: "可显示" },
  ], { storage });
  assert.equal(entries.length, 1);
  assert.equal(entries[0].document.title, "可显示");
  storage.setItem(api.key, "not-json");
  assert.equal(api.read({ storage }).length, 0);
}

function testClearAndStorageFailureDoNotBreakBrowsing() {
  const storage = new Storage();
  api.record("java", { storage, now: 1 });
  assert.equal(api.clear({ storage }), true);
  assert.equal(api.read({ storage }).length, 0);
  const broken = { getItem() { throw new Error("private mode"); }, setItem() { throw new Error("private mode"); } };
  assert.equal(api.read({ storage: broken }).length, 0);
  assert.equal(api.record("java", { storage: broken, now: 2 }), false);
}

[testRecordsNewestFirstAndCapsEntries, testMalformedStorageAndDocumentLookupAreSafe,
  testClearAndStorageFailureDoNotBreakBrowsing].forEach((test) => test());
process.stdout.write("Web reading history: 3/3 passed\n");
