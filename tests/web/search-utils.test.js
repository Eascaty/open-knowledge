const assert = require("assert");

require("../../apps/web/src/search-utils.js");
const search = global.KnowledgeSearch;

const items = [
  {
    id: "doc-java",
    type: "document",
    title: "G1 调优",
    path: ["技术", "程序员", "Java开发"],
    tags: ["Java"],
    summary: "Java 垃圾回收",
    search_text: "g1 调优 java 垃圾回收",
  },
  {
    id: "node-java",
    type: "node",
    title: "Java开发",
    path: ["技术", "程序员", "Java开发"],
    tags: [],
    summary: "",
    search_text: "技术 程序员 java开发",
  },
  {
    id: "doc-ai",
    type: "document",
    title: "智能体记忆",
    path: ["AI", "Agent", "智能体"],
    tags: ["Agent"],
    summary: "",
    search_text: "智能体 agent 记忆",
  },
];

assert.deepStrictEqual(search.tokenizeQuery("  Java  开发 "), ["java  开发", "java", "开发"]);
assert.strictEqual(search.filterLabel("document"), "知识卡");
assert.strictEqual(search.search(items, "java", { filter: "document" }).length, 1);
assert.strictEqual(search.search(items, "java", { filter: "node" })[0].item.id, "node-java");
assert.strictEqual(search.search(items, "不存在").length, 0);
console.log("Web search utils: 5 assertions passed");
