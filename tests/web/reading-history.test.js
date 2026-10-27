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

function testLimitCannotExceedPrivateHistoryBound() {
  const storage = new Storage();
  for (let index = 0; index < 10; index += 1) {
    assert.equal(api.record(`doc-${index}`, { storage, now: index, limit: 100 }), true);
  }
  assert.equal(api.maxLimit, 8);
  assert.equal(api.read({ storage, limit: 100 }).length, 8);
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

const favorites = context.KnowledgeFavorites;
function testFavoritesSurviveNewStoreAndKeepOriginalTime() {
  const storage = new Storage();
  const first = favorites.createStore({ storage, scope: "private" });
  assert.equal(first.set("a", true, 10).ok, true);
  assert.equal(first.set("a", true, 20).ok, true);
  const second = favorites.createStore({ storage, scope: "private" });
  assert.equal(second.has("a"), true);
  assert.equal(second.snapshot().items[0].savedAt, 10);
  assert.equal(second.set("a", false).ok, true);
  assert.equal(first.has("a"), false);
}
function testFavoriteScopeIsolation() {
  const storage = new Storage();
  const a = favorites.createStore({ storage, scope: "/a/private" });
  a.set("same-id", true, 1);
  for (const scope of ["/a/public", "/b/private"]) assert.equal(favorites.createStore({ storage, scope }).has("same-id"), false);
}
function testBrokenFavoritesAreNotOverwritten() {
  const storage = new Storage();
  const store = favorites.createStore({ storage, scope: "test" });
  for (const raw of ["bad-json", '{"version":2,"items":[]}', '{"version":1,"items":[null]}']) {
    storage.setItem(store.key, raw);
    assert.equal(store.set("new", true).ok, false);
    assert.equal(storage.getItem(store.key), raw);
    assert.throws(() => store.exportData());
  }
  const denied = favorites.createStore({ storage: { getItem: () => null, setItem: () => { throw Error("quota"); } }, scope: "x" });
  assert.equal(denied.set("a", true).ok, false);
}
function testFavoriteLimitNeverSilentlyEvicts() {
  const storage = new Storage();
  const store = favorites.createStore({ storage, scope: "test" });
  for (let i = 0; i < 200; i += 1) assert.equal(store.set(`id-${i}`, true, i).ok, true);
  assert.equal(store.set("overflow", true).ok, false);
  assert.equal(store.has("id-0"), true);
  assert.equal(store.set("id-0", true, 999).ok, true);
  store.set("id-0", false);
  assert.equal(store.set("overflow", true).ok, true);
}
function testFavoriteExportContainsOnlyReferences() {
  const storage = new Storage();
  const store = favorites.createStore({ storage, scope: "test" });
  storage.setItem(store.key, JSON.stringify({version:1,items:[{id:"unavailable",savedAt:10,title:"PRIVATE",body:"PRIVATE"}]}));
  assert.deepEqual(JSON.parse(JSON.stringify(store.exportData())), {format:"knowledge-favorites",version:1,items:[{id:"unavailable",savedAt:10}]});
  assert.equal(store.set("", true).ok, false);
  assert.equal(store.set("bad-date", true, -1).ok, false);
  assert.equal(store.has("unavailable"), true);
}
[testFavoritesSurviveNewStoreAndKeepOriginalTime, testFavoriteScopeIsolation, testBrokenFavoritesAreNotOverwritten,
  testFavoriteLimitNeverSilentlyEvicts, testFavoriteExportContainsOnlyReferences].forEach(test => test());

[testRecordsNewestFirstAndCapsEntries, testMalformedStorageAndDocumentLookupAreSafe,
  testLimitCannotExceedPrivateHistoryBound, testClearAndStorageFailureDoNotBreakBrowsing]
  .forEach((test) => test());
process.stdout.write("Web reading history and favorites: 9/9 passed\n");
