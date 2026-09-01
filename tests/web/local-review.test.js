"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/local-review.js", "utf8");

function loadModule(overrides = {}) {
  const context = {
    URL,
    console,
    location: new URL("http://127.0.0.1:8765/"),
    ...overrides,
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "local-review.js" });
  return context.KnowledgeLocalReview;
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === "content-type" ? "application/json" : null },
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
    capabilities: { document_review: true },
  };
}

function changePayload(dryRun, siteRebuilt = true) {
  return {
    ok: true,
    document_id: "doc",
    previous_status: "unverified",
    target_status: "supported",
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

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  focus() {}

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "disabled") this.disabled = true;
    if (name === "value") this.value = String(value);
  }

  addEventListener(name, callback) { this.listeners.set(name, callback); }

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
  createElement(tag) { return new ElementStub(tag); }
}

async function testPublicHostDoesNotProbeOrRender() {
  let requests = 0;
  const api = loadModule();
  const client = new api.LocalReviewClient({
    locationLike: new URL("https://eascaty.github.io/open-knowledge/"),
    fetchImpl: async () => { requests += 1; },
  });
  assert.equal(await client.openSession(), null);
  assert.equal(requests, 0);
  const controller = new api.LocalReviewController({ documentLike: new DocumentStub(), client });
  assert.equal(await controller.initialize(), false);
  assert.equal(controller.renderControl({ id: "doc", status: "unverified" }), null);
}

async function testContractsAreStrict() {
  const api = loadModule();
  assert.equal(api.normalizeSession(sessionPayload()).token, "s".repeat(43));
  assert.equal(api.normalizeSession({ ...sessionPayload(), api_version: "v2" }), null);
  assert.equal(api.normalizeSession({
    ...sessionPayload(), capabilities: { document_review: false },
  }), null);
  assert.equal(api.normalizeChange(changePayload(true)).targetStatus, "supported");
  assert.equal(api.normalizeChange({ ...changePayload(true), target_status: "invented" }), null);
}

async function testClientSendsExpectedStatusAndSession() {
  const requests = [];
  const api = loadModule();
  const client = new api.LocalReviewClient({
    locationLike: new URL("http://127.0.0.1:8765/"),
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/__knowledge/session")) return jsonResponse(sessionPayload());
      return jsonResponse(changePayload(options.body.includes('"action":"preview"')));
    },
  });
  const session = await client.openSession();
  await client.change(session, "preview", "doc", "supported", "unverified");
  assert.equal(requests[0].options.headers["X-Knowledge-Client"], "browser-v1");
  assert.equal(requests[1].options.headers["X-Knowledge-Session"], "s".repeat(43));
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    action: "preview",
    document_id: "doc",
    target_status: "supported",
    expected_status: "unverified",
  });
}

async function testControlPreviewsBeforeApplyAndReloads() {
  const calls = [];
  const notices = [];
  let reloads = 0;
  let waitCalls = 0;
  const api = loadModule();
  const client = {
    openSession: async () => ({ token: "s".repeat(43) }),
    change: async (_session, action, documentId, targetStatus, expectedStatus) => {
      calls.push({ action, documentId, targetStatus, expectedStatus });
      return api.normalizeChange(changePayload(action === "preview", action === "preview"));
    },
    waitUntilPublished: async () => { waitCalls += 1; return true; },
  };
  const controller = new api.LocalReviewController({
    documentLike: new DocumentStub(),
    client,
    notify: (message) => notices.push(message),
    reload: () => { reloads += 1; },
  });
  await controller.initialize();
  const card = controller.renderControl({ id: "doc", status: "unverified" });
  await card.find("review-toggle").emit("click");
  const select = card.find("review-select");
  select.value = "supported";
  await select.emit("change");
  await card.find("review-form").emit("submit");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].action, "preview");
  assert.equal(reloads, 0);
  await card.find("review-apply").emit("click");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].expectedStatus, "unverified");
  assert.equal(waitCalls, 1);
  assert.equal(reloads, 1);
  assert.match(notices.at(-1), /有证据支持/);
}

Promise.resolve()
  .then(testPublicHostDoesNotProbeOrRender)
  .then(testContractsAreStrict)
  .then(testClientSendsExpectedStatusAndSession)
  .then(testControlPreviewsBeforeApplyAndReloads)
  .then(() => process.stdout.write("Web local review: 4/4 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
