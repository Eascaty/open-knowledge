"use strict";

(function exposeLocalClassification(global) {
  const API_VERSION = "browser-v1";
  const SERVICE_NAME = "personal-knowledge-manager";
  const SESSION_PATH = "/__knowledge/session";
  const CLASSIFICATION_PATH = "/__knowledge/classification";
  const STATUS_PATH = "/__knowledge/status";
  const SESSION_HEADER = "X-Knowledge-Session";
  const CLIENT_HEADER = "X-Knowledge-Client";
  const UNDO_KEY = "knowledge-classification-undo-v1";
  const UNDO_MAX_AGE_MS = 15 * 60 * 1000;

  class LocalClassificationError extends Error {
    constructor(message, { status = 0, code = "request_failed" } = {}) {
      super(message);
      this.name = "LocalClassificationError";
      this.status = status;
      this.code = code;
    }
  }

  function isLoopbackLocation(locationLike) {
    if (!locationLike || locationLike.protocol !== "http:") return false;
    return locationLike.hostname === "127.0.0.1" || locationLike.hostname === "localhost";
  }

  function normalizeSession(payload) {
    if (
      !payload
      || payload.ok !== true
      || payload.schema_version !== 1
      || payload.api_version !== API_VERSION
      || payload.service !== SERVICE_NAME
      || typeof payload.session_token !== "string"
      || payload.session_token.length < 32
      || payload.capabilities?.manual_classification !== true
    ) return null;
    return { token: payload.session_token };
  }

  function safePath(value) {
    return Array.isArray(value) && value.every((item) => typeof item === "string")
      ? [...value]
      : null;
  }

  function normalizeCorrection(payload) {
    if (!payload || payload.ok !== true) return null;
    const previousPath = safePath(payload.previous_path);
    const targetPath = safePath(payload.target_path);
    if (
      typeof payload.document_id !== "string"
      || typeof payload.previous_node_id !== "string"
      || typeof payload.target_node_id !== "string"
      || typeof payload.changed !== "boolean"
      || typeof payload.dry_run !== "boolean"
      || typeof payload.site_rebuilt !== "boolean"
      || typeof payload.rebuild_pending !== "boolean"
      || !previousPath
      || !targetPath
    ) return null;
    return {
      documentId: payload.document_id,
      previousNodeId: payload.previous_node_id,
      targetNodeId: payload.target_node_id,
      previousPath,
      targetPath,
      changed: payload.changed,
      dryRun: payload.dry_run,
      siteRebuilt: payload.site_rebuilt,
      rebuildPending: payload.rebuild_pending,
    };
  }

  function safeErrorMessage(payload, fallback) {
    const message = payload?.error?.message;
    return typeof message === "string" && message.length <= 300 ? message : fallback;
  }

  function rememberUndo(record) {
    try {
      global.sessionStorage?.setItem(UNDO_KEY, JSON.stringify({
        ...record,
        savedAt: Date.now(),
      }));
    } catch {
      // Storage can be disabled without disabling classification correction.
    }
  }

  function readUndo(documentItem) {
    try {
      const record = JSON.parse(global.sessionStorage?.getItem(UNDO_KEY) || "null");
      const savedAt = Number(record?.savedAt);
      if (
        !record
        || record.documentId !== documentItem?.id
        || record.toNodeId !== documentItem?.node_id
        || typeof record.fromNodeId !== "string"
        || !record.fromNodeId
        || !safePath(record.fromPath)
        || !Number.isFinite(savedAt)
        || savedAt <= 0
        || Date.now() - savedAt > UNDO_MAX_AGE_MS
      ) return null;
      return record;
    } catch {
      return null;
    }
  }

  function clearUndo() {
    try {
      global.sessionStorage?.removeItem(UNDO_KEY);
    } catch {
      // Storage can be disabled without disabling classification correction.
    }
  }

  class LocalClassificationClient {
    constructor({ fetchImpl, locationLike } = {}) {
      this.fetch = fetchImpl || global.fetch?.bind(global);
      this.location = locationLike || global.location;
    }

    endpoint(path) {
      return new URL(path, this.location.href).toString();
    }

    async jsonRequest(path, options, fallback) {
      if (!this.fetch) throw new LocalClassificationError("浏览器不支持本地归类请求");
      let response;
      try {
        response = await this.fetch(this.endpoint(path), {
          cache: "no-store",
          credentials: "same-origin",
          ...options,
        });
      } catch {
        throw new LocalClassificationError(fallback);
      }
      const contentType = response.headers?.get("content-type") || "";
      if (!contentType.toLocaleLowerCase().startsWith("application/json")) {
        throw new LocalClassificationError(fallback, { status: response.status });
      }
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new LocalClassificationError(safeErrorMessage(payload, fallback), {
          status: response.status,
          code: payload?.error?.code,
        });
      }
      return payload;
    }

    async openSession() {
      if (!isLoopbackLocation(this.location)) return null;
      try {
        const payload = await this.jsonRequest(
          SESSION_PATH,
          {
            method: "POST",
            headers: { Accept: "application/json", [CLIENT_HEADER]: API_VERSION },
          },
          "无法连接本地知识管家",
        );
        return normalizeSession(payload);
      } catch {
        return null;
      }
    }

    async correct(session, action, documentId, targetNodeId, expectedNodeId) {
      const payload = await this.jsonRequest(
        CLASSIFICATION_PATH,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            [SESSION_HEADER]: session.token,
          },
          body: JSON.stringify({
            action,
            document_id: documentId,
            target_node_id: targetNodeId,
            expected_node_id: expectedNodeId,
          }),
        },
        action === "preview" ? "无法预览归类变化" : "无法保存归类变化",
      );
      const correction = normalizeCorrection(payload);
      if (!correction) throw new LocalClassificationError("知识管家返回了无效的归类结果");
      return correction;
    }

    async waitUntilPublished(attempts = 30) {
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        await new Promise((resolve) => global.setTimeout(resolve, 700));
        try {
          const payload = await this.jsonRequest(
            STATUS_PATH,
            { method: "GET", headers: { Accept: "application/json" } },
            "网站更新状态暂时不可用",
          );
          if (
            payload?.service === SERVICE_NAME
            && typeof payload.successful_inbox_fingerprint === "string"
            && payload.successful_inbox_fingerprint
            && ["running", "degraded"].includes(payload.status)
          ) return true;
        } catch {
          // The manager may be swapping the checked site; continue the bounded wait.
        }
      }
      return false;
    }
  }

  function createElement(documentLike, tag, attributes = {}, ...children) {
    const element = documentLike.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (key === "className") element.className = value;
      else if (key === "text") element.textContent = value;
      else if (key === "hidden") element.hidden = Boolean(value);
      else element.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child) element.append(child);
    return element;
  }

  function pathText(path) {
    return Array.isArray(path) ? path.join(" / ") : "未归类";
  }

  class LocalClassificationController {
    constructor({ documentLike, client, notify, reload } = {}) {
      this.document = documentLike || global.document;
      this.client = client || new LocalClassificationClient();
      this.notify = typeof notify === "function" ? notify : () => {};
      this.reload = typeof reload === "function" ? reload : () => global.location?.reload();
      this.session = null;
    }

    async initialize() {
      this.session = await this.client.openSession();
      return Boolean(this.session);
    }

    async reloadWhenReady(result, message) {
      this.notify(message);
      const published = result.siteRebuilt || await this.client.waitUntilPublished();
      if (published) {
        this.reload();
        return true;
      }
      this.notify("归类已保存；知识管家仍在安全更新网站");
      return false;
    }

    renderControl(documentItem, nodes) {
      if (!this.session || !this.document || !documentItem?.id || !documentItem?.node_id) return null;
      const choices = (Array.isArray(nodes) ? nodes : [])
        .filter((node) => node?.id && node.parent_id !== null && node.id !== documentItem.node_id)
        .sort((left, right) => pathText(left.path).localeCompare(pathText(right.path), "zh-CN"));
      if (!choices.length) return null;

      const create = (tag, attributes, ...children) => createElement(
        this.document, tag, attributes, ...children,
      );
      const currentPath = pathText(documentItem.path);
      const current = create("p", { className: "classification-current", text: currentPath });
      const undoRecord = readUndo(documentItem);
      const undo = undoRecord ? create("button", {
        className: "classification-undo",
        type: "button",
        text: "撤销上次调整",
      }) : null;
      const toggle = create("button", {
        className: "classification-toggle",
        type: "button",
        text: "调整归类",
        "aria-expanded": "false",
      });
      const select = create("select", {
        className: "classification-select",
        "aria-label": "选择新的主分类",
      });
      select.append(create("option", { value: "", text: "请选择目标分类" }));
      for (const node of choices) {
        select.append(create("option", { value: node.id, text: pathText(node.path) }));
      }
      const previewButton = create("button", {
        className: "classification-preview",
        type: "submit",
        text: "预览变化",
        disabled: "disabled",
      });
      const cancel = create("button", {
        className: "classification-cancel",
        type: "button",
        text: "取消",
      });
      const actions = create("div", { className: "classification-actions" }, previewButton, cancel);
      const form = create(
        "form",
        { className: "classification-form", hidden: true },
        create("label", { text: "移动到" }, select),
        create("small", { text: "先预览，不会立即保存。" }),
        actions,
      );
      const preview = create("div", {
        className: "classification-confirmation",
        hidden: true,
        role: "status",
      });
      const card = create(
        "div",
        { className: "classification-card" },
        current,
        undo,
        toggle,
        form,
        preview,
      );
      let previewedTarget = null;
      let busy = false;

      const resetPreview = () => {
        previewedTarget = null;
        preview.hidden = true;
        preview.replaceChildren();
      };
      const setBusy = (value) => {
        busy = value;
        select.disabled = value;
        previewButton.disabled = value || !select.value;
        cancel.disabled = value;
      };
      const close = () => {
        if (busy) return;
        form.hidden = true;
        toggle.hidden = false;
        toggle.setAttribute("aria-expanded", "false");
        select.value = "";
        resetPreview();
        setBusy(false);
      };

      toggle.addEventListener("click", () => {
        toggle.hidden = true;
        form.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        select.focus();
      });
      cancel.addEventListener("click", close);
      select.addEventListener("change", () => {
        resetPreview();
        previewButton.disabled = !select.value;
      });
      if (undo) {
        let undoConfirmed = false;
        undo.addEventListener("click", async () => {
          if (busy) return;
          if (!undoConfirmed) {
            undoConfirmed = true;
            undo.textContent = `确认恢复到：${pathText(undoRecord.fromPath)}`;
            undo.classList?.add("confirming");
            return;
          }
          setBusy(true);
          undo.disabled = true;
          undo.textContent = "正在撤销并更新网站…";
          try {
            const result = await this.client.correct(
              this.session,
              "apply",
              documentItem.id,
              undoRecord.fromNodeId,
              documentItem.node_id,
            );
            clearUndo();
            const reloaded = await this.reloadWhenReady(result, "上次归类调整已撤销");
            if (!reloaded) undo.textContent = "已撤销，稍后刷新即可查看";
          } catch (error) {
            undo.disabled = false;
            undoConfirmed = false;
            undo.textContent = "撤销上次调整";
            this.notify(error instanceof Error ? error.message : "无法撤销归类变化");
          } finally {
            setBusy(false);
          }
        });
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (busy || !select.value) return;
        setBusy(true);
        previewButton.textContent = "正在预览…";
        try {
          const result = await this.client.correct(
            this.session, "preview", documentItem.id, select.value, documentItem.node_id,
          );
          previewedTarget = result.targetNodeId;
          const confirm = create("button", {
            className: "classification-apply",
            type: "button",
            text: "确认移动",
          });
          preview.replaceChildren(
            create("span", { text: result.previousPath.join(" / ") }),
            create("b", { text: "→", "aria-hidden": "true" }),
            create("strong", { text: result.targetPath.join(" / ") }),
            confirm,
          );
          preview.hidden = false;
          confirm.addEventListener("click", async () => {
            if (busy || previewedTarget !== select.value) return;
            setBusy(true);
            confirm.disabled = true;
            confirm.textContent = "正在保存并更新网站…";
            try {
              const applied = await this.client.correct(
                this.session, "apply", documentItem.id, select.value, documentItem.node_id,
              );
              rememberUndo({
                documentId: documentItem.id,
                fromNodeId: applied.previousNodeId,
                toNodeId: applied.targetNodeId,
                fromPath: applied.previousPath,
                toPath: applied.targetPath,
              });
              const reloaded = await this.reloadWhenReady(
                applied,
                "归类已保存，正在打开更新后的知识卡",
              );
              if (!reloaded) {
                confirm.textContent = "已保存，稍后刷新即可查看";
              }
            } catch (error) {
              confirm.disabled = false;
              confirm.textContent = "确认移动";
              this.notify(error instanceof Error ? error.message : "无法保存归类变化");
            } finally {
              setBusy(false);
            }
          });
        } catch (error) {
          this.notify(error instanceof Error ? error.message : "无法预览归类变化");
        } finally {
          previewButton.textContent = "预览变化";
          setBusy(false);
        }
      });
      return card;
    }
  }

  let controller = null;

  async function mount(options = {}) {
    if (controller) return controller;
    controller = new LocalClassificationController(options);
    await controller.initialize();
    return controller;
  }

  function renderControl(documentItem, nodes) {
    return controller?.renderControl(documentItem, nodes) || null;
  }

  global.KnowledgeLocalClassification = Object.freeze({
    API_VERSION,
    LocalClassificationClient,
    LocalClassificationController,
    LocalClassificationError,
    isLoopbackLocation,
    mount,
    normalizeCorrection,
    normalizeSession,
    readUndo,
    renderControl,
  });
})(typeof window !== "undefined" ? window : globalThis);
