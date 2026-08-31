"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/local-ingest.js", "utf8");

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function loadLocalIngest(overrides = {}) {
  const context = {
    URL,
    console,
    clearTimeout,
    location: new URL("http://127.0.0.1:8765/"),
    sessionStorage: memoryStorage(),
    setTimeout,
    ...overrides,
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "local-ingest.js" });
  return { api: context.KnowledgeLocalIngest, context };
}

function jsonResponse(payload, status = 200, contentType = "application/json; charset=utf-8") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLocaleLowerCase() === "content-type" ? contentType : null },
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
    capabilities: {
      file_upload: true,
      maximum_file_bytes: 64 * 1024 * 1024,
      accepted_extensions: [".md", ".txt", ".pdf"],
      history_limit: 24,
    },
  };
}

async function testPublicHostNeverProbesOrShowsWriteCapability() {
  let requested = 0;
  const { api } = loadLocalIngest();
  const client = new api.LocalManagerClient({
    fetchImpl: async () => {
      requested += 1;
      throw new Error("should not request");
    },
    locationLike: new URL("https://eascaty.github.io/open-knowledge/"),
  });
  assert.equal(await client.openSession(), null);
  assert.equal(requested, 0);

  const html = fs.readFileSync("apps/web/src/index.html", "utf8");
  const app = fs.readFileSync("apps/web/src/app.js", "utf8");
  const styles = fs.readFileSync("apps/web/src/local-ingest.css", "utf8");
  assert.match(html, /id="ingest-trigger"[^>]*hidden/);
  assert.match(html, /id="ingest-dialog"[^>]*hidden/);
  assert.doesNotMatch(app, /style\.overflow/);
  assert.match(styles, /body\.search-open/);
}

async function testSessionProbeStrictlyValidatesManagerContract() {
  const invalidPayloads = [
    { ...sessionPayload(), api_version: "v2" },
    { ...sessionPayload(), service: "unrelated-service" },
    { ...sessionPayload(), session_token: "short" },
    { ...sessionPayload(), capabilities: { ...sessionPayload().capabilities, file_upload: false } },
  ];
  const { api } = loadLocalIngest();
  for (const payload of invalidPayloads) {
    const client = new api.LocalManagerClient({
      fetchImpl: async () => jsonResponse(payload),
      locationLike: new URL("http://127.0.0.1:8765/"),
    });
    assert.equal(await client.openSession(), null);
  }

  const htmlClient = new api.LocalManagerClient({
    fetchImpl: async () => jsonResponse({}, 200, "text/html"),
    locationLike: new URL("http://127.0.0.1:8765/"),
  });
  assert.equal(await htmlClient.openSession(), null);
}

async function testSessionAndInboxUsePostAndSessionHeaders() {
  const requests = [];
  const { api } = loadLocalIngest();
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/__knowledge/session")) return jsonResponse(sessionPayload());
    return jsonResponse({
      ok: true,
      schema_version: 1,
      service: "personal-knowledge-manager",
      manager_status: "updating",
      poll_after_ms: 650,
      uploads: [],
    });
  };
  const client = new api.LocalManagerClient({
    fetchImpl,
    locationLike: new URL("http://127.0.0.1:8765/"),
  });
  const session = await client.openSession();
  assert.equal(session.token, "s".repeat(43));
  assert.equal(session.maximumBytes, 64 * 1024 * 1024);
  assert.equal(session.acceptedExtensions.length, 3);
  const inbox = await client.inbox(session);
  assert.equal(inbox.manager.status, "updating");
  assert.equal(inbox.pollAfterMs, 650);
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Knowledge-Client"], "browser-v1");
  assert.equal(requests[1].options.method, "POST");
  assert.equal(requests[1].options.headers["X-Knowledge-Session"], session.token);
}

async function testRawUploadReportsProgressAndNormalizesReceipt() {
  class EventTargetStub {
    constructor() {
      this.listeners = new Map();
    }

    addEventListener(name, callback) {
      this.listeners.set(name, callback);
    }

    emit(name, value = {}) {
      this.listeners.get(name)?.(value);
    }
  }

  class XhrStub extends EventTargetStub {
    constructor() {
      super();
      this.upload = new EventTargetStub();
      this.headers = {};
    }

    open(method, url, async) {
      this.method = method;
      this.url = url;
      this.async = async;
    }

    setRequestHeader(name, value) {
      this.headers[name] = value;
    }

    get responseText() {
      throw new Error("InvalidStateError: responseType is json");
    }

    send(body) {
      this.body = body;
      this.upload.emit("progress", { lengthComputable: true, loaded: 5, total: 10 });
      this.status = 202;
      this.response = {
        ok: true,
        upload: {
          upload_id: "upload-1",
          filename: body.name,
          size_bytes: body.size,
          received_at: "2026-08-23T12:00:00Z",
          status: "queued",
        },
      };
      this.emit("load");
    }
  }

  const xhr = new XhrStub();
  const { api } = loadLocalIngest();
  const client = new api.LocalManagerClient({
    xhrFactory: () => xhr,
    locationLike: new URL("http://127.0.0.1:8765/"),
  });
  const file = { name: "智能体 笔记.md", size: 10 };
  const progress = [];
  const receipt = await client.upload(
    file,
    { token: "s".repeat(43) },
    (value) => progress.push(value),
  );
  assert.equal(xhr.method, "POST");
  assert.equal(xhr.url, "http://127.0.0.1:8765/__knowledge/upload");
  assert.equal(xhr.headers["Content-Type"], "application/octet-stream");
  assert.equal(xhr.headers["X-Knowledge-Session"], "s".repeat(43));
  assert.equal(xhr.headers["X-Knowledge-Filename"], encodeURIComponent(file.name));
  assert.equal(xhr.body, file);
  assert.deepEqual(progress, [50]);
  assert.equal(receipt.uploadId, "upload-1");
  assert.equal(receipt.status, "queued");
}

async function testCompletedResultCarriesDocumentTarget() {
  const { api } = loadLocalIngest();
  const completed = api.normalizeUpload({
    upload_id: "upload-2",
    filename: "G1.md",
    size_bytes: 100,
    status: "completed",
    result: {
      outcome: "created",
      site_revision: "revision-2",
      document: {
        id: "doc-g1",
        title: "G1 垃圾收集器",
        node_id: "java",
        path: ["技术", "程序员", "Java开发"],
      },
    },
  });
  assert.equal(completed.outcome, "created");
  assert.equal(completed.siteRevision, "revision-2");
  assert.equal(completed.documentId, "doc-g1");
  assert.equal(completed.path.length, 3);
}

async function testRetryableFailureKeepsPollingState() {
  const { api, context } = loadLocalIngest();
  const retryable = api.normalizeUpload({
    upload_id: "upload-retry",
    filename: "retry.md",
    size_bytes: 30,
    status: "failed",
    error: { code: "processing_failed", retryable: true },
  });
  assert.equal(retryable.retryable, true);
  assert.equal(retryable.error, "自动整理失败，知识管家会继续重试");
  assert.equal(api.isTerminal(retryable), false);
  assert.equal(api.isTerminal({ status: "failed", retryable: false }), true);
  const controller = new api.LocalIngestController({
    documentLike: null,
    windowLike: context,
    client: {},
  });
  controller.entries = [retryable];
  assert.equal(controller.hasPendingWork(), true);
  controller.entries = [{ status: "failed", retryable: false }];
  assert.equal(controller.hasPendingWork(), false);
}

async function testSessionStorageRestoresHistory() {
  const { api, context } = loadLocalIngest();
  const controller = new api.LocalIngestController({
    documentLike: null,
    windowLike: context,
    client: {},
  });
  controller.entries = [{
    id: "upload-3",
    uploadId: "upload-3",
    name: "Agent.md",
    size: 20,
    status: "processing",
    progress: 100,
    path: [],
  }];
  controller.persist();
  const restored = new api.LocalIngestController({
    documentLike: null,
    windowLike: context,
    client: {},
  });
  restored.restore();
  assert.equal(restored.entries.length, 1);
  assert.equal(restored.entries[0].uploadId, "upload-3");
  assert.equal(restored.entries[0].status, "processing");

  controller.entries = [{
    id: "local-interrupted",
    uploadId: null,
    name: "unfinished.md",
    size: 12,
    status: "queued",
    progress: 0,
    path: [],
  }];
  controller.persist();
  const interrupted = new api.LocalIngestController({
    documentLike: null,
    windowLike: context,
    client: {},
  });
  interrupted.restore();
  assert.equal(interrupted.entries[0].status, "failed");
  assert.equal(interrupted.entries[0].retryable, false);
}

async function testPendingDocumentSurvivesOldSnapshotRace() {
  const { api } = loadLocalIngest();
  api.rememberPendingDocument("doc-agent");

  const oldSnapshot = new Map();
  const pendingInOldSnapshot = api.peekPendingDocumentId();
  if (oldSnapshot.has(pendingInOldSnapshot)) api.clearPendingDocumentId();
  assert.equal(api.peekPendingDocumentId(), "doc-agent");

  const refreshedSnapshot = new Map([["doc-agent", { id: "doc-agent" }]]);
  const pendingInRefreshedSnapshot = api.peekPendingDocumentId();
  if (refreshedSnapshot.has(pendingInRefreshedSnapshot)) api.clearPendingDocumentId();
  assert.equal(api.peekPendingDocumentId(), null);

  const app = fs.readFileSync("apps/web/src/app.js", "utf8");
  assert.match(app, /peekPendingDocumentId\(\)/);
  assert.match(app, /pendingDocumentId && state\.documents\.has\(pendingDocumentId\)/);
  assert.match(app, /state\.activeDocumentId === pendingDocumentId[\s\S]*clearPendingDocumentId\(\)/);
  assert.doesNotMatch(app, /consumePendingDocumentId/);
}

async function testCapabilityFormatHintOnlyAdvertisesAvailableFormats() {
  const { api } = loadLocalIngest();
  const withoutPdf = api.capabilityFormatText([".md", ".java", ".docx"]);
  assert.match(withoutPdf, /文本与 Markdown/);
  assert.match(withoutPdf, /代码与配置/);
  assert.match(withoutPdf, /DOCX/);
  assert.doesNotMatch(withoutPdf, /PDF|pdftotext/i);

  const withPdf = api.capabilityFormatText([".md", ".pdf"]);
  assert.match(withPdf, /PDF/);
  assert.match(withPdf, /pdftotext/);

  const html = fs.readFileSync("apps/web/src/index.html", "utf8");
  assert.match(html, /id="ingest-format-hint">正在确认这台电脑支持的文件类型/);
  assert.doesNotMatch(html, /id="ingest-format-hint">[^<]*PDF/);
  assert.match(
    source,
    /formatHint\.textContent = capabilityFormatText\(this\.session\.acceptedExtensions\)/,
  );
}

Promise.resolve()
  .then(testPublicHostNeverProbesOrShowsWriteCapability)
  .then(testSessionProbeStrictlyValidatesManagerContract)
  .then(testSessionAndInboxUsePostAndSessionHeaders)
  .then(testRawUploadReportsProgressAndNormalizesReceipt)
  .then(testCompletedResultCarriesDocumentTarget)
  .then(testRetryableFailureKeepsPollingState)
  .then(testSessionStorageRestoresHistory)
  .then(testPendingDocumentSurvivesOldSnapshotRace)
  .then(testCapabilityFormatHintOnlyAdvertisesAvailableFormats)
  .then(() => process.stdout.write("Web local ingest: 9/9 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
