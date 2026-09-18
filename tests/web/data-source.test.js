"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/data-source.js", "utf8");

function loadDataSources(responses) {
  const requested = [];
  const context = {
    URLSearchParams,
    document: { title: "测试知识库", documentElement: { lang: "zh-CN" } },
    fetch: async (url) => {
      requested.push(url);
      const key = Object.keys(responses).find((candidate) => url.startsWith(candidate));
      if (!key) return { ok: false, status: 404, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => responses[key] };
    },
  };
  context.window = context;
  vm.runInNewContext(source, context, { filename: "data-source.js" });
  return { dataSources: context.KnowledgeDataSources, requested };
}

async function testStaticBundleAdapter() {
  const responses = {
    "./data/site-data.json": { root: "root" },
    "./data/search-index.json": { items: [] },
    "./data/graph.json": { nodes: [] },
  };
  const { dataSources, requested } = loadDataSources(responses);
  const result = await new dataSources.StaticBundleDataSource().loadWorkspace();
  assert.equal(result.data.root, "root");
  assert.deepEqual(requested.sort(), Object.keys(responses).sort());
}

async function testApiAdapterProducesWorkspaceContract() {
  const envelope = (data) => ({ api_version: "v1", data });
  const responses = {
    "./api/v1/taxonomy": envelope([
      {
        id: "root",
        parent_id: null,
        name: "知识",
        path: ["知识"],
        locked: true,
        children: [
          {
            id: "java",
            parent_id: "root",
            name: "Java",
            path: ["知识", "Java"],
            locked: false,
            children: [],
          },
        ],
      },
    ]),
    "./api/v1/documents?": envelope({
      page: 0,
      size: 100,
      total: 1,
      items: [{ id: "doc" }],
    }),
    "./api/v1/documents/doc": envelope({
      id: "doc",
      title: "G1",
      body: "正文",
      summary: "摘要",
      tags: ["Java"],
      node_id: "java",
      path: ["知识", "Java"],
      updated_at: "2026-08-10T00:00:00Z",
      source: { kind: "file", original_name: "g1.md", sha256: "abc" },
      relations: [],
    }),
  };
  const { dataSources } = loadDataSources(responses);
  const result = await new dataSources.ApiKnowledgeDataSource().loadWorkspace();
  assert.equal(result.data.schema_version, 1);
  assert.equal(result.data.documents[0].content, "正文");
  assert.equal(result.data.nodes[0].document_count, 1);
  assert.equal(result.search.items.length, 3);
  assert.equal(result.search.items.find((item) => item.id === "doc").status, "unverified");
  assert.equal(result.graph.tree_edges.length, 1);
}

Promise.resolve()
  .then(testStaticBundleAdapter)
  .then(testApiAdapterProducesWorkspaceContract)
  .then(() => process.stdout.write("Web data-source adapters: 2/2 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
