"use strict";

(function exposeLocalIngest(global) {
  const API_VERSION = "browser-v1";
  const SERVICE_NAME = "personal-knowledge-manager";
  const SESSION_PATH = "/__knowledge/session";
  const STATUS_PATH = "/__knowledge/inbox";
  const UPLOAD_PATH = "/__knowledge/upload";
  const SESSION_HEADER = "X-Knowledge-Session";
  const CLIENT_HEADER = "X-Knowledge-Client";
  const FILENAME_HEADER = "X-Knowledge-Filename";
  const HISTORY_KEY = "knowledge-local-ingest-v1";
  const PENDING_DOCUMENT_KEY = "knowledge-open-document-v1";
  const HISTORY_LIMIT = 24;
  const MAX_BATCH_FILES = 20;
  const MAX_PASTE_CHARACTERS = 200000;

  class LocalIngestError extends Error {
    constructor(message, { status = 0, code = "request_failed" } = {}) {
      super(message);
      this.name = "LocalIngestError";
      this.status = status;
      this.code = code;
    }
  }

  function isLoopbackLocation(locationLike) {
    if (!locationLike || locationLike.protocol !== "http:") return false;
    return locationLike.hostname === "127.0.0.1" || locationLike.hostname === "localhost";
  }

  function storageGet(key) {
    try {
      return global.sessionStorage?.getItem(key) || null;
    } catch {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      global.sessionStorage?.setItem(key, value);
    } catch {
      // Storage can be disabled without disabling local ingestion.
    }
  }

  function storageRemove(key) {
    try {
      global.sessionStorage?.removeItem(key);
    } catch {
      // Storage can be disabled without disabling local ingestion.
    }
  }

  function rememberPendingDocument(documentId) {
    if (typeof documentId !== "string" || !documentId || documentId.length > 300) return;
    storageSet(PENDING_DOCUMENT_KEY, documentId);
  }

  function peekPendingDocumentId() {
    return storageGet(PENDING_DOCUMENT_KEY);
  }

  function clearPendingDocumentId() {
    storageRemove(PENDING_DOCUMENT_KEY);
  }

  function safeErrorMessage(payload, fallback) {
    const message = payload?.error?.message;
    return typeof message === "string" && message.length <= 300 ? message : fallback;
  }

  function normalizeSession(payload) {
    if (
      !payload
      || payload.ok !== true
      || payload.schema_version !== 1
      || payload.api_version !== API_VERSION
      || payload.service !== SERVICE_NAME
    ) return null;
    const capabilities = payload.capabilities;
    if (!capabilities || typeof capabilities !== "object") return null;
    if (capabilities.file_upload !== true) return null;
    if (typeof payload.session_token !== "string" || payload.session_token.length < 32) return null;
    if (
      !Number.isSafeInteger(capabilities.maximum_file_bytes)
      || capabilities.maximum_file_bytes <= 0
    ) {
      return null;
    }
    if (
      !Array.isArray(capabilities.accepted_extensions)
      || !capabilities.accepted_extensions.length
    ) {
      return null;
    }
    const acceptedExtensions = capabilities.accepted_extensions.filter(
      (item) => typeof item === "string" && /^\.[a-z0-9+_-]{1,15}$/i.test(item),
    );
    if (!acceptedExtensions.length) return null;
    return {
      token: payload.session_token,
      maximumBytes: capabilities.maximum_file_bytes,
      acceptedExtensions: [...new Set(acceptedExtensions.map((item) => item.toLocaleLowerCase()))],
      historyLimit: Number.isSafeInteger(capabilities.history_limit)
        ? capabilities.history_limit
        : HISTORY_LIMIT,
    };
  }

  function normalizedStatus(value) {
    return ["queued", "processing", "completed", "failed"].includes(value)
      ? value
      : "queued";
  }

  function isTerminal(entry) {
    return entry.status === "completed" || (entry.status === "failed" && !entry.retryable);
  }

  function normalizeUpload(payload) {
    if (!payload || typeof payload.upload_id !== "string" || !payload.upload_id) return null;
    const result = payload.result && typeof payload.result === "object" ? payload.result : {};
    const documentResult = result.document && typeof result.document === "object"
      ? result.document
      : {};
    const outcome = result.outcome === "created" || result.outcome === "duplicate"
      ? result.outcome
      : null;
    const errorCode = typeof payload.error?.code === "string" ? payload.error.code : null;
    const retryable = Boolean(payload.error?.retryable);
    const error = typeof payload.error === "string"
      ? payload.error
      : typeof payload.error?.message === "string"
        ? payload.error.message
        : errorCode === "processing_failed"
          ? "自动整理失败，知识管家会继续重试"
        : null;
    return {
      id: payload.upload_id,
      uploadId: payload.upload_id,
      name: typeof payload.filename === "string" ? payload.filename : "未命名资料",
      size: Number.isSafeInteger(payload.size_bytes) ? payload.size_bytes : 0,
      status: normalizedStatus(payload.status),
      progress: 100,
      receivedAt: typeof payload.received_at === "string" ? payload.received_at : "",
      startedAt: typeof payload.started_at === "string" ? payload.started_at : "",
      completedAt: typeof payload.completed_at === "string" ? payload.completed_at : "",
      error: error && error.length <= 300 ? error : null,
      retryable,
      outcome,
      siteRevision: typeof result.site_revision === "string" ? result.site_revision : "",
      documentId: typeof documentResult.id === "string" ? documentResult.id : null,
      documentTitle: typeof documentResult.title === "string" ? documentResult.title : null,
      nodeId: typeof documentResult.node_id === "string" ? documentResult.node_id : null,
      path: Array.isArray(documentResult.path)
        ? documentResult.path.filter((item) => typeof item === "string")
        : [],
    };
  }

  function normalizeInbox(payload) {
    if (
      !payload
      || payload.ok !== true
      || payload.schema_version !== 1
      || payload.service !== SERVICE_NAME
      || !Array.isArray(payload.uploads)
    ) return null;
    const manager = payload.manager && typeof payload.manager === "object"
      ? { ...payload.manager }
      : {};
    manager.status = typeof manager.status === "string"
      ? manager.status
      : typeof payload.manager_status === "string"
        ? payload.manager_status
        : "unknown";
    return {
      uploads: payload.uploads.map(normalizeUpload).filter(Boolean),
      manager,
      pollAfterMs: Number.isSafeInteger(payload.poll_after_ms)
        ? Math.max(400, Math.min(5000, payload.poll_after_ms))
        : 1200,
    };
  }

  function fileExtension(name) {
    const match = String(name || "").toLocaleLowerCase().match(/(\.[^.]+)$/);
    return match ? match[1] : "";
  }

  function capabilityFormatText(extensions) {
    const advertised = new Set(
      (Array.isArray(extensions) ? extensions : [])
        .filter((item) => typeof item === "string" && /^\.[a-z0-9+_-]{1,15}$/i.test(item))
        .map((item) => item.toLocaleLowerCase()),
    );
    const groups = [
      ["文本与 Markdown", [".md", ".txt", ".rst", ".csv", ".log"]],
      [
        "代码与配置",
        [
          ".c", ".cc", ".conf", ".cpp", ".css", ".go", ".h", ".ini", ".java",
          ".js", ".json", ".properties", ".py", ".rb", ".sh", ".sql", ".toml",
          ".ts", ".tsx", ".xml", ".yaml", ".yml",
        ],
      ],
      ["HTML", [".html", ".htm"]],
      ["DOCX", [".docx"]],
    ];
    const labels = [];
    for (const [label, groupExtensions] of groups) {
      if (!groupExtensions.some((extension) => advertised.has(extension))) continue;
      labels.push(label);
      for (const extension of groupExtensions) advertised.delete(extension);
    }
    if (advertised.delete(".pdf")) labels.push("PDF（需要本机 pdftotext）");
    const remaining = [...advertised];
    labels.push(...remaining.slice(0, 6).map((extension) => extension.slice(1).toLocaleUpperCase()));
    if (remaining.length > 6) labels.push(`另 ${remaining.length - 6} 种格式`);
    return labels.length
      ? `本机支持：${labels.join("、")}`
      : "本机尚未提供可投放的文件格式";
  }

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function statusText(entry) {
    if (entry.status === "uploading") return `上传中 ${Math.round(entry.progress || 0)}%`;
    if (entry.status === "queued") return entry.uploadId ? "等待知识管家整理" : "等待上传";
    if (entry.status === "processing") return "正在提取、分类并构建";
    if (entry.status === "failed" && entry.retryable) {
      return entry.error || "处理未完成，知识管家会自动重试";
    }
    if (entry.status === "failed") return entry.error || "处理失败，可以重新投放";
    if (entry.outcome === "duplicate") return "内容重复，已关联原知识";
    if (entry.status === "completed") return "已进入知识网络";
    return "等待处理";
  }

  function newLocalId() {
    if (global.crypto?.randomUUID) return `local-${global.crypto.randomUUID()}`;
    return `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function normalizedPasteTitle(value) {
    return String(value || "")
      .normalize("NFC")
      .replace(/[\r\n]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 120);
  }

  function pastedNoteFilename(now = new Date()) {
    const suppliedTime = now && typeof now.getTime === "function" ? Number(now.getTime()) : NaN;
    const date = new Date(Number.isFinite(suppliedTime) ? suppliedTime : Date.now());
    const stamp = date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
    return `粘贴笔记-${stamp}.md`;
  }

  function createPastedNoteFile(title, content, { FileCtor = global.File, now } = {}) {
    const body = String(content || "").replace(/\r\n?/g, "\n").trim();
    if (!body) {
      throw new LocalIngestError("请先粘贴要整理的内容", { code: "empty_note" });
    }
    if (body.length > MAX_PASTE_CHARACTERS) {
      throw new LocalIngestError("粘贴内容超过 200,000 字限制", { code: "note_too_large" });
    }
    if (typeof FileCtor !== "function") {
      throw new LocalIngestError("当前浏览器不支持直接粘贴笔记", {
        code: "unsupported_browser",
      });
    }
    const noteTitle = normalizedPasteTitle(title);
    const markdown = `${noteTitle ? `# ${noteTitle}\n\n` : ""}${body}\n`;
    const suppliedTime = now && typeof now.getTime === "function" ? Number(now.getTime()) : NaN;
    return new FileCtor([markdown], pastedNoteFilename(now), {
      type: "text/markdown;charset=utf-8",
      lastModified: Number.isFinite(suppliedTime) ? suppliedTime : Date.now(),
    });
  }

  class LocalManagerClient {
    constructor({ fetchImpl, xhrFactory, locationLike } = {}) {
      this.fetchImpl = fetchImpl || global.fetch?.bind(global);
      this.xhrFactory = xhrFactory || (() => new global.XMLHttpRequest());
      this.location = locationLike || global.location;
    }

    endpoint(path) {
      return new URL(path, this.location.origin).href;
    }

    async postJson(path, headers = {}) {
      if (!this.fetchImpl) throw new LocalIngestError("浏览器不支持本地投放请求");
      const response = await this.fetchImpl(this.endpoint(path), {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", ...headers },
      });
      const contentType = response.headers?.get?.("Content-Type") || "";
      if (!contentType.toLocaleLowerCase().includes("application/json")) {
        throw new LocalIngestError("当前页面不是可写入的本地知识库", {
          status: response.status,
          code: "not_local_manager",
        });
      }
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new LocalIngestError("本地知识管家返回了无效状态", {
          status: response.status,
          code: "invalid_response",
        });
      }
      if (!response.ok || payload?.ok !== true) {
        throw new LocalIngestError(safeErrorMessage(payload, "本地知识管家暂时不可用"), {
          status: response.status,
          code: payload?.error?.code || "request_failed",
        });
      }
      return payload;
    }

    async openSession() {
      if (!isLoopbackLocation(this.location)) return null;
      try {
        const payload = await this.postJson(SESSION_PATH, { [CLIENT_HEADER]: API_VERSION });
        return normalizeSession(payload);
      } catch {
        return null;
      }
    }

    async inbox(session) {
      const payload = await this.postJson(STATUS_PATH, { [SESSION_HEADER]: session.token });
      const normalized = normalizeInbox(payload);
      if (!normalized) {
        throw new LocalIngestError("本地知识管家状态格式无效", {
          code: "invalid_response",
        });
      }
      return normalized;
    }

    upload(file, session, onProgress = () => {}) {
      return new Promise((resolve, reject) => {
        const xhr = this.xhrFactory();
        xhr.open("POST", this.endpoint(UPLOAD_PATH), true);
        xhr.responseType = "json";
        xhr.timeout = 120000;
        xhr.setRequestHeader("Accept", "application/json");
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        xhr.setRequestHeader(SESSION_HEADER, session.token);
        xhr.setRequestHeader(FILENAME_HEADER, encodeURIComponent(file.name));
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable && event.total > 0) {
            onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
          }
        });
        xhr.addEventListener("load", () => {
          const payload = xhr.response;
          if (xhr.status < 200 || xhr.status >= 300 || payload?.ok !== true) {
            reject(new LocalIngestError(safeErrorMessage(payload, "文件投放失败"), {
              status: xhr.status,
              code: payload?.error?.code || "upload_failed",
            }));
            return;
          }
          const upload = normalizeUpload(payload.upload);
          if (!upload) {
            reject(new LocalIngestError("文件已发送，但回执格式无效", {
              code: "invalid_response",
            }));
            return;
          }
          resolve(upload);
        });
        xhr.addEventListener("error", () => {
          reject(new LocalIngestError("无法连接本地知识管家", { code: "network_error" }));
        });
        xhr.addEventListener("timeout", () => {
          reject(new LocalIngestError("文件接收超时，请重新投放", { code: "timeout" }));
        });
        xhr.send(file);
      });
    }
  }

  class LocalIngestController {
    constructor({ documentLike, windowLike, client, notify } = {}) {
      this.document = documentLike || global.document;
      this.window = windowLike || global;
      this.client = client || new LocalManagerClient();
      this.notify = typeof notify === "function" ? notify : () => {};
      this.session = null;
      this.entries = [];
      this.dismissed = new Set();
      this.files = new Map();
      this.activeUploads = 0;
      this.pollTimer = null;
      this.refreshing = false;
      this.reloadScheduled = false;
      this.lastFocused = null;
      this.managerState = {};
      this.pollAfterMs = 1200;
    }

    queryUi() {
      const byId = (id) => this.document?.getElementById(id);
      this.ui = {
        trigger: byId("ingest-trigger"),
        triggerState: byId("ingest-trigger-state"),
        dialog: byId("ingest-dialog"),
        dropzone: byId("ingest-dropzone"),
        formatHint: byId("ingest-format-hint"),
        input: byId("ingest-file-input"),
        paste: byId("ingest-paste"),
        pasteForm: byId("ingest-paste-form"),
        pasteTitle: byId("ingest-paste-title"),
        pasteContent: byId("ingest-paste-content"),
        pasteHint: byId("ingest-paste-hint"),
        pasteSubmit: byId("ingest-paste-submit"),
        pipeline: byId("ingest-pipeline"),
        pipelineTitle: byId("ingest-pipeline-title"),
        pipelineDetail: byId("ingest-pipeline-detail"),
        list: byId("ingest-file-list"),
        empty: byId("ingest-empty"),
        clear: byId("ingest-clear"),
        announcer: byId("ingest-announcer"),
      };
      return Object.values(this.ui).every(Boolean);
    }

    restore() {
      let payload;
      try {
        payload = JSON.parse(storageGet(HISTORY_KEY) || "{}");
      } catch {
        payload = {};
      }
      const storedEntries = Array.isArray(payload.entries) ? payload.entries : [];
      this.entries = storedEntries.slice(0, HISTORY_LIMIT).map((item) => {
        const interrupted = !item.uploadId && ["queued", "uploading"].includes(item.status);
        const status = interrupted ? "failed" : item.status;
        return {
          ...item,
          id: typeof item.id === "string" ? item.id : newLocalId(),
          status,
          retryable: interrupted ? false : Boolean(item.retryable),
          progress: Number.isFinite(item.progress) ? item.progress : 0,
          error: interrupted
            ? "页面刷新前上传未完成，请重新投放"
            : item.error || null,
        };
      });
      this.dismissed = new Set(
        Array.isArray(payload.dismissed)
          ? payload.dismissed.filter((item) => typeof item === "string").slice(0, 48)
          : [],
      );
    }

    persist() {
      const entries = this.entries.slice(0, HISTORY_LIMIT).map((entry) => ({
        id: entry.id,
        uploadId: entry.uploadId || null,
        name: entry.name,
        size: entry.size,
        status: entry.status,
        progress: entry.progress,
        receivedAt: entry.receivedAt || "",
        startedAt: entry.startedAt || "",
        completedAt: entry.completedAt || "",
        error: entry.error || null,
        retryable: Boolean(entry.retryable),
        outcome: entry.outcome || null,
        siteRevision: entry.siteRevision || "",
        documentId: entry.documentId || null,
        documentTitle: entry.documentTitle || null,
        nodeId: entry.nodeId || null,
        path: entry.path || [],
      }));
      storageSet(HISTORY_KEY, JSON.stringify({ version: 1, entries, dismissed: [...this.dismissed] }));
    }

    bind() {
      this.ui.trigger.addEventListener("click", () => this.openDialog());
      this.ui.dropzone.addEventListener("click", () => this.ui.input.click());
      this.ui.input.addEventListener("change", () => {
        this.enqueueFiles(this.ui.input.files || []);
        this.ui.input.value = "";
      });
      this.ui.pasteContent.addEventListener("input", () => this.updatePasteState());
      this.ui.pasteForm.addEventListener("submit", (event) => {
        event.preventDefault();
        this.submitPastedNote();
      });
      this.ui.clear.addEventListener("click", () => this.clearTerminal());
      for (const closer of this.document.querySelectorAll("[data-close-ingest]")) {
        closer.addEventListener("click", () => this.closeDialog());
      }
      this.document.addEventListener("keydown", (event) => this.handleKeydown(event), true);
      this.document.addEventListener("dragenter", (event) => this.handleDrag(event));
      this.document.addEventListener("dragover", (event) => this.handleDrag(event));
      this.document.addEventListener("dragleave", (event) => this.handleDragLeave(event));
      this.document.addEventListener("drop", (event) => this.handleDrop(event));
      this.window.addEventListener("knowledge:close-ingest", () => this.closeDialog(false));
    }

    async initialize() {
      if (!this.queryUi()) return false;
      this.restore();
      this.bind();
      this.render();
      this.session = await this.client.openSession();
      if (!this.session) return false;
      this.ui.input.accept = this.session.acceptedExtensions.join(",");
      this.ui.formatHint.textContent = capabilityFormatText(this.session.acceptedExtensions);
      this.ui.paste.hidden = (
        !this.session.acceptedExtensions.includes(".md")
        || typeof this.window.File !== "function"
      );
      this.updatePasteState();
      this.ui.trigger.hidden = false;
      await this.refreshStatus(true);
      this.ensurePolling();
      return true;
    }

    announce(message) {
      this.ui.announcer.textContent = "";
      this.window.setTimeout(() => {
        this.ui.announcer.textContent = message;
      }, 20);
    }

    openDialog() {
      if (!this.session) return;
      this.window.dispatchEvent(new Event("knowledge:close-search"));
      this.window.dispatchEvent(new Event("knowledge:close-review-queue"));
      this.lastFocused = this.document.activeElement;
      this.ui.dialog.hidden = false;
      this.document.body.classList.add("ingest-open");
      this.window.setTimeout(() => this.ui.dropzone.focus(), 0);
      void this.refreshStatus(true);
    }

    closeDialog(restoreFocus = true) {
      if (this.ui.dialog.hidden) return;
      this.ui.dialog.hidden = true;
      this.ui.dropzone.classList.remove("drag-active");
      this.document.body.classList.remove("ingest-open");
      if (restoreFocus && this.lastFocused?.focus) this.lastFocused.focus();
    }

    focusableDialogItems() {
      return [...this.ui.dialog.querySelectorAll(
        "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary",
      )]
        .filter((item) => {
          if (item.tabIndex < 0 || item.hidden || item.closest("[hidden]")) return false;
          const closedDetails = item.closest("details:not([open])");
          return !closedDetails || item.tagName === "SUMMARY";
        });
    }

    handleKeydown(event) {
      if (this.ui.dialog.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        this.closeDialog();
        return;
      }
      if (event.key !== "Tab") return;
      const items = this.focusableDialogItems();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && this.document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && this.document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    hasFileDrag(event) {
      return this.session && [...(event.dataTransfer?.types || [])].includes("Files");
    }

    handleDrag(event) {
      if (!this.hasFileDrag(event)) return;
      event.preventDefault();
      if (this.ui.dialog.hidden) this.openDialog();
      this.ui.dropzone.classList.add("drag-active");
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    }

    handleDragLeave(event) {
      if (event.relatedTarget) return;
      this.ui.dropzone.classList.remove("drag-active");
    }

    handleDrop(event) {
      if (!this.hasFileDrag(event)) return;
      event.preventDefault();
      this.ui.dropzone.classList.remove("drag-active");
      this.enqueueFiles(event.dataTransfer?.files || []);
    }

    validateFile(file) {
      if (!file || typeof file.name !== "string" || !file.name) return "文件名无效";
      if (!Number.isFinite(file.size) || file.size <= 0) return "不能投入空文件";
      if (file.size > this.session.maximumBytes) {
        return `文件超过 ${formatBytes(this.session.maximumBytes)} 限制`;
      }
      const extension = fileExtension(file.name);
      if (extension && !this.session.acceptedExtensions.includes(extension)) {
        return `暂不支持 ${extension} 文件`;
      }
      return null;
    }

    updatePasteState() {
      const length = this.ui.pasteContent.value.length;
      this.ui.pasteHint.textContent = `${length.toLocaleString("zh-CN")} / ${MAX_PASTE_CHARACTERS.toLocaleString("zh-CN")} 字；会作为 Markdown 进入同一条自动整理链路`;
      this.ui.pasteSubmit.disabled = !this.ui.pasteContent.value.trim();
    }

    submitPastedNote() {
      let file;
      try {
        file = createPastedNoteFile(
          this.ui.pasteTitle.value,
          this.ui.pasteContent.value,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : "无法创建粘贴笔记";
        this.notify(message);
        this.announce(message);
        this.ui.pasteContent.focus();
        return;
      }
      const error = this.validateFile(file);
      if (error) {
        this.notify(error);
        this.announce(error);
        return;
      }
      const outcome = this.enqueueFiles([file]);
      if (outcome.accepted !== 1) return;
      this.ui.pasteTitle.value = "";
      this.ui.pasteContent.value = "";
      this.ui.paste.open = false;
      this.updatePasteState();
      this.announce("粘贴笔记已加入，开始投放");
    }

    enqueueFiles(fileList) {
      const files = [...fileList].slice(0, MAX_BATCH_FILES);
      let rejected = 0;
      const added = [];
      const selected = new Set();
      for (const file of files) {
        const selectionKey = `${file.name}\u0000${file.size}\u0000${file.lastModified || 0}`;
        if (selected.has(selectionKey)) continue;
        selected.add(selectionKey);
        const error = this.validateFile(file);
        const entry = {
          id: newLocalId(),
          uploadId: null,
          name: file.name,
          size: file.size,
          status: error ? "failed" : "queued",
          progress: 0,
          error,
          retryable: false,
          outcome: null,
          documentId: null,
          path: [],
        };
        if (error) rejected += 1;
        else this.files.set(entry.id, file);
        added.push(entry);
      }
      this.entries = [...added, ...this.entries].slice(0, HISTORY_LIMIT);
      this.persist();
      this.render();
      if (added.length - rejected > 0) {
        this.announce(`已加入 ${added.length - rejected} 个文件，开始投放`);
      }
      if (rejected > 0) this.notify(`${rejected} 个文件不符合投放要求`);
      this.pumpQueue();
      return { added: added.length, accepted: added.length - rejected, rejected };
    }

    nextQueuedEntry() {
      return this.entries.find(
        (entry) => entry.status === "queued" && !entry.uploadId && this.files.has(entry.id),
      );
    }

    pumpQueue() {
      while (this.activeUploads < 2) {
        const entry = this.nextQueuedEntry();
        if (!entry) break;
        this.activeUploads += 1;
        entry.status = "uploading";
        this.render();
        void this.uploadEntry(entry).finally(() => {
          this.activeUploads -= 1;
          this.persist();
          this.render();
          this.pumpQueue();
          this.ensurePolling();
        });
      }
    }

    async uploadEntry(entry, retried = false) {
      const file = this.files.get(entry.id);
      if (!file) return;
      try {
        const receipt = await this.client.upload(file, this.session, (progress) => {
          entry.progress = progress;
          this.render();
        });
        this.files.delete(entry.id);
        this.dismissed.delete(receipt.uploadId);
        Object.assign(entry, receipt, { id: receipt.uploadId });
        this.announce(`${entry.name} 已接收，等待知识管家整理`);
      } catch (error) {
        if (!retried && error instanceof LocalIngestError && error.status === 403) {
          const renewed = await this.client.openSession();
          if (renewed) {
            this.session = renewed;
            await this.uploadEntry(entry, true);
            return;
          }
        }
        this.files.delete(entry.id);
        entry.status = "failed";
        entry.error = error instanceof Error ? error.message : "文件投放失败";
        entry.retryable = false;
        this.announce(`${entry.name} 投放失败`);
      }
    }

    mergeStatus(status) {
      const existing = this.entries.find((entry) => entry.uploadId === status.uploadId);
      if (existing) {
        const previous = existing.status;
        Object.assign(existing, status);
        return previous !== "completed" && status.status === "completed" ? existing : null;
      }
      if (!this.dismissed.has(status.uploadId)) this.entries.push(status);
      return null;
    }

    async refreshStatus(quiet = false, retried = false) {
      if (!this.session || this.refreshing) return;
      this.refreshing = true;
      try {
        const status = await this.client.inbox(this.session);
        this.managerState = status.manager;
        this.pollAfterMs = status.pollAfterMs;
        const completed = [];
        for (const upload of status.uploads) {
          const transition = this.mergeStatus(upload);
          if (transition) completed.push(transition);
        }
        this.entries = this.entries.slice(0, HISTORY_LIMIT);
        this.persist();
        this.render();
        if (completed.length) this.handleCompleted(completed);
      } catch (error) {
        if (!retried && error instanceof LocalIngestError && error.status === 403) {
          const renewed = await this.client.openSession();
          if (renewed) {
            this.session = renewed;
            this.refreshing = false;
            await this.refreshStatus(quiet, true);
            return;
          }
        }
        if (!quiet) this.notify(error instanceof Error ? error.message : "无法读取处理状态");
      } finally {
        this.refreshing = false;
      }
    }

    handleCompleted(entries) {
      const created = entries.find((entry) => entry.outcome === "created" && entry.documentId);
      const target = created || entries.find((entry) => entry.documentId);
      for (const entry of entries) {
        const result = entry.outcome === "duplicate" ? "已识别为重复内容" : "已进入知识网络";
        this.announce(`${entry.name} ${result}`);
      }
      this.notify(entries.length === 1 ? `${entries[0].name} 已整理完成` : `${entries.length} 份资料已整理完成`);
      if (this.reloadScheduled) return;
      this.reloadScheduled = true;
      if (target?.documentId) rememberPendingDocument(target.documentId);
      this.window.setTimeout(() => this.window.location.reload(), 700);
    }

    hasPendingWork() {
      return this.activeUploads > 0 || this.entries.some(
        (entry) => !isTerminal(entry),
      );
    }

    ensurePolling() {
      if (this.pollTimer || !this.hasPendingWork()) return;
      this.pollTimer = this.window.setTimeout(async () => {
        this.pollTimer = null;
        await this.refreshStatus(true);
        this.ensurePolling();
      }, this.pollAfterMs);
    }

    clearTerminal() {
      for (const entry of this.entries) {
        if (isTerminal(entry) && entry.uploadId) {
          this.dismissed.add(entry.uploadId);
        }
      }
      this.entries = this.entries.filter((entry) => !isTerminal(entry));
      this.persist();
      this.render();
    }

    renderPipeline() {
      const managerStatus = this.managerState.status;
      const pending = this.entries.some((entry) => !isTerminal(entry));
      let status = "ready";
      let title = "知识管家已就绪";
      let detail = "选择文件或粘贴文字后将自动开始处理";
      if (this.activeUploads > 0) {
        status = "pending";
        title = "正在接收资料";
        detail = `还有 ${this.activeUploads} 个文件正在传入本机`;
      } else if (managerStatus === "updating" || pending) {
        status = "updating";
        title = managerStatus === "updating" ? "正在整理知识" : "资料已排队";
        detail = managerStatus === "updating"
          ? "正在提取、去重、分类并更新知识地图"
          : "知识管家将在文件稳定后自动开始";
      } else if (managerStatus === "degraded" || this.managerState.last_error) {
        status = "failed";
        title = "最近一次整理未完成";
        detail = this.managerState.last_error || "原有知识网站仍可正常使用";
      } else if (this.managerState.last_success_at) {
        detail = "最近一次自动整理已完成";
      }
      this.ui.pipeline.dataset.status = status;
      this.ui.pipelineTitle.textContent = title;
      this.ui.pipelineDetail.textContent = detail;
      this.ui.trigger.classList.toggle("busy", status === "pending" || status === "updating");
    }

    renderEntry(entry) {
      const item = this.document.createElement("li");
      item.className = "ingest-file-item";
      item.dataset.status = entry.status;
      if (entry.outcome) item.dataset.outcome = entry.outcome;

      const kind = this.document.createElement("span");
      kind.className = "ingest-file-kind";
      kind.textContent = fileExtension(entry.name).slice(1, 5) || "FILE";

      const main = this.document.createElement("div");
      main.className = "ingest-file-main";
      const name = this.document.createElement("strong");
      name.className = "ingest-file-name";
      name.textContent = entry.documentTitle || entry.name;
      name.title = entry.name;
      const meta = this.document.createElement("span");
      meta.className = "ingest-file-meta";
      meta.textContent = [formatBytes(entry.size), ...(entry.path || [])].filter(Boolean).join(" · ");
      const progress = this.document.createElement("progress");
      progress.className = "ingest-file-progress";
      progress.max = 100;
      progress.setAttribute("aria-label", `${entry.name}：${statusText(entry)}`);
      if (entry.status === "processing") progress.removeAttribute("value");
      else progress.value = entry.status === "completed" ? 100 : entry.progress || 0;
      main.append(name, meta, progress);

      const status = this.document.createElement("span");
      status.className = "ingest-file-status";
      status.textContent = statusText(entry);
      item.append(kind, main, status);
      return item;
    }

    render() {
      if (!this.ui) return;
      this.ui.list.replaceChildren(...this.entries.map((entry) => this.renderEntry(entry)));
      this.ui.empty.hidden = this.entries.length > 0;
      this.ui.clear.hidden = !this.entries.some((entry) => isTerminal(entry));
      this.renderPipeline();
    }
  }

  let controller = null;

  async function mount(options = {}) {
    if (controller) return controller;
    controller = new LocalIngestController(options);
    await controller.initialize();
    return controller;
  }

  global.KnowledgeLocalIngest = Object.freeze({
    API_VERSION,
    LocalIngestError,
    LocalManagerClient,
    LocalIngestController,
    capabilityFormatText,
    clearPendingDocumentId,
    createPastedNoteFile,
    isTerminal,
    isLoopbackLocation,
    mount,
    normalizeInbox,
    normalizeSession,
    normalizeUpload,
    peekPendingDocumentId,
    rememberPendingDocument,
  });
})(typeof window !== "undefined" ? window : globalThis);
