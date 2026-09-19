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
    status: "supported",
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

assert.deepStrictEqual(search.tokenizeQuery("  Java  开发 "), ["java", "开发"]);
assert.strictEqual(search.filterLabel("document"), "知识卡");
assert.strictEqual(search.search(items, "java", { filter: "document" }).length, 1);
assert.strictEqual(search.search(items, "java", { filter: "node" })[0].item.id, "node-java");
assert.strictEqual(search.search(items, "不存在").length, 0);
// Fixed fictional target queries: separated keywords, scopes and empty recovery.
assert.strictEqual(search.search(items, "java 回收")[0].item.id, "doc-java");
assert.strictEqual(search.search(items, "java", { path: ["AI"] }).length, 0);
assert.strictEqual(search.search(items, "java", { path: ["技术", "程序员"], tag: "Java" })[0].item.id, "doc-java");
assert.strictEqual(search.search(items, "", { tag: "Agent" })[0].item.id, "doc-ai");
assert.strictEqual(search.search(items, "java", { tag: "java" }).length, 0);
assert.strictEqual(search.search(items, "java", { path: ["技术员"] }).length, 0);
assert.strictEqual(search.search(items, "java", { filter: "node", tag: "Java" }).length, 0);
assert.deepStrictEqual(search.facetOptions(items).tags, ["Agent", "Java"]);
assert.strictEqual(search.facetOptions(items).paths.filter(path => path.join("/") === "技术/程序员").length, 1);
assert.deepStrictEqual(search.search(items, "java", { path: [], tag: "" }), search.search(items, "java"));
assert.strictEqual(search.search(items, "java", { limit: 1 }).length, 1);
assert.deepStrictEqual(search.facetOptions(items).statuses, [
  { value: "unverified", label: "待验证", count: 1 },
  { value: "supported", label: "有证据支持", count: 1 },
]);
assert.strictEqual(search.search(items, "", { status: "supported" })[0].item.id, "doc-java");
assert.strictEqual(search.search(items, "", { status: "unverified" })[0].item.id, "doc-ai");
assert.strictEqual(search.search(items, "java", { status: "unverified" }).length, 0);
assert.strictEqual(search.search(items, "", { filter: "node", status: "supported" }).length, 0);
const literal = search.highlightParts("C++ <script>Java</script> a.b", "c++ java a.b");
assert.deepStrictEqual(literal.filter(part => part.matched).map(part => part.text), ["C++", "Java", "a.b"]);
assert.strictEqual(literal.map(part => part.text).join(""), "C++ <script>Java</script> a.b");
assert.deepStrictEqual(search.highlightParts("中文全文搜索", "中文 中文全文")[0], {text:"中文全文",matched:true});
assert.deepStrictEqual(search.highlightParts("Java", ""), [{text:"Java",matched:false}]);
const longBody = "前文".repeat(200) + "目标线索" + "后文".repeat(200);
const snippet = search.resultSnippet({summary:"短摘要",search_text:"目标线索"}, "目标线索", longBody);
assert.ok(snippet.startsWith("…") && snippet.endsWith("…") && snippet.includes("目标线索") && snippet.length <= 182);
assert.strictEqual(search.resultSnippet({summary:"Java 概述"}, "", ""), "Java 概述");
const ranked = search.search(items, "java");
assert.deepStrictEqual(search.search(items, "java", {offset:1,limit:1}), ranked.slice(1,2));
assert.deepStrictEqual(search.search(items, "java", {offset:-1}), ranked);
console.log("Web search utils: 29 assertions passed");
