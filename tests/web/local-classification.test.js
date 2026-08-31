"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/local-classification.js", "utf8");

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function loadModule(overrides = {}) {
  const context = {
    URL,
    console,
    clearTimeout,
    setTimeout,
    location: new URL("http://127.0.0.1:8765/"),
    ...overrides,
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "local-classification.js" });
  return { api: context.KnowledgeLocalClassification, context };
}

function jsonResponse(payload, status = 200, contentType = "application/json; charset=utf-8") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === "content-type" ? contentType : null },
    json: async () => payload,
  };
}

function sessionPayload() {
  return {
    ok: true,
    schema_version: 1,
    api_version: "browser-v1",
    service: "personal-knowledge-manager",
    session_token: "s".repeat(43),
    capabilities: { manual_classification: true },
  };
}

function correctionPayload(dryRun, siteRebuilt = true) {
  return {
    ok: true,
    document_id: "doc",
    previous_node_id: "old",
    target_node_id: "new",
    previous_path: ["知识", "旧分类"],
    target_path: ["知识", "新分类"],
    changed: true,
    dry_run: dryRun,
    site_rebuilt: siteRebuilt,
    rebuild_pending: !siteRebuilt,
  };
}

class ElementStub {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "disabled") this.disabled = true;
    if (name === "value") this.value = String(value);
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback);
  }

  focus() {}

  async emit(name, event = {}) {
    return this.listeners.get(name)?.({ preventDefault() {}, ...event });
  }

  find(className) {
    if (this.className === className) return this;
    for (const child of this.children) {
      const found = child?.find?.(className);
      if (found) return found;
    }
    return null;
  }
}

class DocumentStub {
  createElement(tag) {
    return new ElementStub(tag);
  }
}

async function testPublicHostNeverProbesOrRendersWriteControl() {
  let requested = 0;
  const { api } = loadModule();
  const client = new api.LocalClassificationClient({
    fetchImpl: async () => {
      requested += 1;
      throw new Error("should not request");
    },
    locationLike: new URL("https://eascaty.github.io/open-knowledge/"),
  });
  assert.equal(await client.openSession(), null);
  assert.equal(requested, 0);
  const controller = new api.LocalClassificationController({
    documentLike: new DocumentStub(),
    client,
  });
  assert.equal(await controller.initialize(), false);
  assert.equal(controller.renderControl({ id: "doc", node_id: "old" }, []), null);
}

async function testSessionAndCorrectionContractsAreStrict() {
  const { api } = loadModule();
  assert.equal(api.normalizeSession(sessionPayload()).token, "s".repeat(43));
  assert.equal(api.normalizeSession({ ...sessionPayload(), api_version: "v2" }), null);
  assert.equal(
    api.normalizeSession({
      ...sessionPayload(),
      capabilities: { manual_classification: false },
    }),
    null,
  );
  assert.equal(api.normalizeCorrection(correctionPayload(true)).dryRun, true);
  assert.equal(api.normalizeCorrection({ ...correctionPayload(true), previous_path: "bad" }), null);
}

async function testClientUsesSameOriginSessionAndExpectedNode() {
  const requests = [];
  const { api } = loadModule();
  const client = new api.LocalClassificationClient({
    locationLike: new URL("http://127.0.0.1:8765/"),
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/__knowledge/session")) return jsonResponse(sessionPayload());
      return jsonResponse(correctionPayload(options.body.includes('"action":"preview"')));
    },
  });
  const session = await client.openSession();
  const result = await client.correct(session, "preview", "doc", "new", "old");
  assert.equal(result.targetNodeId, "new");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Knowledge-Client"], "browser-v1");
  assert.equal(requests[1].options.headers["X-Knowledge-Session"], "s".repeat(43));
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    action: "preview",
    document_id: "doc",
    target_node_id: "new",
    expected_node_id: "old",
  });
}

async function testControlPreviewsBeforeApplyAndReloadsCheckedSite() {
  const calls = [];
  let reloads = 0;
  const notices = [];
  const sessionStorage = memoryStorage();
  const { api } = loadModule({ sessionStorage });
  const client = {
    openSession: async () => ({ token: "s".repeat(43) }),
    correct: async (_session, action, documentId, targetNodeId, expectedNodeId) => {
      calls.push({ action, documentId, targetNodeId, expectedNodeId });
      return api.normalizeCorrection(correctionPayload(action === "preview"));
    },
    waitUntilPublished: async () => true,
  };
  const controller = new api.LocalClassificationController({
    documentLike: new DocumentStub(),
    client,
    notify: (message) => notices.push(message),
    reload: () => { reloads += 1; },
  });
  await controller.initialize();
  const card = controller.renderControl(
    { id: "doc", node_id: "old", path: ["知识", "旧分类"] },
    [
      { id: "root", parent_id: null, path: ["知识"] },
      { id: "old", parent_id: "root", path: ["知识", "旧分类"] },
      { id: "new", parent_id: "root", path: ["知识", "新分类"] },
    ],
  );
  const toggle = card.find("classification-toggle");
  const select = card.find("classification-select");
  const form = card.find("classification-form");
  await toggle.emit("click");
  select.value = "new";
  await select.emit("change");
  await form.emit("submit");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].action, "preview");
  assert.equal(reloads, 0);
  const confirm = card.find("classification-apply");
  await confirm.emit("click");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].action, "apply");
  assert.equal(calls[1].expectedNodeId, "old");
  assert.equal(reloads, 1);
  assert.match(notices.at(-1), /归类已保存/);

  const refreshed = controller.renderControl(
    { id: "doc", node_id: "new", path: ["知识", "新分类"] },
    [
      { id: "root", parent_id: null, path: ["知识"] },
      { id: "old", parent_id: "root", path: ["知识", "旧分类"] },
      { id: "new", parent_id: "root", path: ["知识", "新分类"] },
    ],
  );
  const undo = refreshed.find("classification-undo");
  assert.ok(undo);
  await undo.emit("click");
  assert.match(undo.textContent, /确认恢复到/);
  await undo.emit("click");
  assert.equal(calls.length, 3);
  assert.equal(calls[2].targetNodeId, "old");
  assert.equal(calls[2].expectedNodeId, "new");
  assert.equal(reloads, 2);
  assert.equal(api.readUndo({ id: "doc", node_id: "new" }), null);
}

Promise.resolve()
  .then(testPublicHostNeverProbesOrRendersWriteControl)
  .then(testSessionAndCorrectionContractsAreStrict)
  .then(testClientUsesSameOriginSessionAndExpectedNode)
  .then(testControlPreviewsBeforeApplyAndReloadsCheckedSite)
  .then(() => process.stdout.write("Web local classification: 4/4 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
