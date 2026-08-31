"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/related-documents.js", "utf8");

function loadRelatedDocuments() {
  const context = { console };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "related-documents.js" });
  return context.KnowledgeRelatedDocuments;
}

function documentItem(id, overrides = {}) {
  return {
    id,
    title: id,
    node_id: "java-jvm",
    path: ["技术", "程序员", "Java开发", "JVM"],
    source_id: `source-${id}`,
    tags: [],
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function nodes() {
  return [
    { id: "java-jvm", parent_id: "java" },
    { id: "java-concurrency", parent_id: "java" },
    { id: "ai-agent", parent_id: "ai" },
  ];
}

function testRankingIsDeterministicAndExplainable() {
  const api = loadRelatedDocuments();
  const current = documentItem("current", {
    source_id: "source-long-java",
    tags: ["JVM", "G1", "GC"],
  });
  const sameSource = documentItem("same-source", {
    source_id: "source-long-java",
    node_id: "java-concurrency",
    path: ["技术", "程序员", "Java开发", "并发"],
  });
  const sameNodeAndTags = documentItem("same-node-tags", {
    tags: ["g1", "GC"],
  });
  const adjacent = documentItem("adjacent", {
    node_id: "java-concurrency",
    path: ["技术", "程序员", "Java开发", "并发"],
  });
  const unrelated = documentItem("unrelated", {
    node_id: "ai-agent",
    path: ["AI", "Agent", "智能体"],
  });

  const ranked = api.rankRelatedDocuments(
    current,
    [unrelated, adjacent, sameNodeAndTags, sameSource, current],
    nodes(),
  );
  assert.deepEqual(
    Array.from(ranked, (item) => item.document.id),
    ["same-source", "same-node-tags", "adjacent"],
  );
  assert.equal(ranked[0].reason, "同一原文");
  assert.match(ranked[1].reason, /共同标签/);
  assert.equal(ranked[2].reason, "相邻专业方向");
  assert.equal(ranked.some((item) => item.document.id === "unrelated"), false);
}

function testLimitAndMalformedInputsFailClosed() {
  const api = loadRelatedDocuments();
  const current = documentItem("current", { tags: ["Java"] });
  const candidates = Array.from({ length: 30 }, (_, index) => documentItem(`doc-${index}`, {
    tags: ["java"],
    updated_at: `2026-08-${String((index % 28) + 1).padStart(2, "0")}T00:00:00Z`,
  }));
  assert.equal(api.rankRelatedDocuments(current, candidates, nodes(), 3).length, 3);
  assert.equal(api.rankRelatedDocuments(null, candidates, nodes()).length, 0);
  assert.equal(api.rankRelatedDocuments(current, null, nodes()).length, 0);
  assert.equal(api.scoreRelatedDocument(current, current, nodes()), null);

  const stableTie = api.rankRelatedDocuments(current, [
    documentItem("tie-b", { title: "B", tags: ["java"] }),
    documentItem("tie-a", { title: "A", tags: ["java"] }),
  ], nodes());
  assert.deepEqual(Array.from(stableTie, (item) => item.document.id), ["tie-a", "tie-b"]);

  const topLevel = documentItem("technology", {
    node_id: "technology",
    path: ["知识", "技术"],
  });
  const otherTopLevel = documentItem("finance", {
    node_id: "finance",
    path: ["知识", "金融"],
  });
  const rootSiblings = [
    { id: "technology", parent_id: "root" },
    { id: "finance", parent_id: "root" },
  ];
  assert.equal(api.scoreRelatedDocument(topLevel, otherTopLevel, rootSiblings), null);
}

Promise.resolve()
  .then(testRankingIsDeterministicAndExplainable)
  .then(testLimitAndMalformedInputsFailClosed)
  .then(() => process.stdout.write("Web related knowledge: 2/2 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
