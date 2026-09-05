"use strict";

(function exposeLocalReview(global) {
  const API_VERSION = "browser-v1";
  const SERVICE_NAME = "personal-knowledge-manager";
  const SESSION_PATH = "/__knowledge/session";
  const REVIEW_PATH = "/__knowledge/review";
  const STATUS_PATH = "/__knowledge/status";
  const SESSION_HEADER = "X-Knowledge-Session";
  const CLIENT_HEADER = "X-Knowledge-Client";
  const REVIEW_OPTIONS = Object.freeze([
    { value: "unverified", label: "待验证", detail: "尚未进行人工判断" },
    { value: "personal", label: "个人认知", detail: "个人经验或观点，不作为外部事实" },
    { value: "supported", label: "有证据支持", detail: "已有来源证据支持当前内容" },
    { value: "verified-by-practice", label: "实践验证", detail: "已经在实际工作或项目中验证" },
    { value: "contradicted", label: "存在争议", detail: "证据或实践结论存在冲突" },
    { value: "deprecated", label: "已过时", detail: "内容不再适用于当前环境" },
  ]);

  class LocalReviewError extends Error {
    constructor(message, { status = 0, code = "request_failed" } = {}) {
      super(message);
      this.name = "LocalReviewError";
      this.status = status;
      this.code = code;
    }
  }

  function optionFor(status) {
    return REVIEW_OPTIONS.find((item) => item.value === status) || REVIEW_OPTIONS[0];
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
      || payload.capabilities?.document_review !== true
    ) return null;
    return { token: payload.session_token };
  }

  function normalizeChange(payload) {
    if (
      !payload
      || payload.ok !== true
      || typeof payload.document_id !== "string"
      || typeof payload.previous_status !== "string"
      || typeof payload.target_status !== "string"
      || typeof payload.changed !== "boolean"
      || typeof payload.dry_run !== "boolean"
      || typeof payload.site_rebuilt !== "boolean"
      || typeof payload.rebuild_pending !== "boolean"
      || !REVIEW_OPTIONS.some((item) => item.value === payload.previous_status)
      || !REVIEW_OPTIONS.some((item) => item.value === payload.target_status)
    ) return null;
    return {
      documentId: payload.document_id,
      previousStatus: payload.previous_status,
      targetStatus: payload.target_status,
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

  class LocalReviewClient {
    constructor({ fetchImpl, locationLike } = {}) {
      this.fetch = fetchImpl || global.fetch?.bind(global);
      this.location = locationLike || global.location;
    }

    endpoint(path) {
      return new URL(path, this.location.href).toString();
    }

    async jsonRequest(path, options, fallback) {
      if (!this.fetch) throw new LocalReviewError("浏览器不支持本地审核请求");
      let response;
      try {
        response = await this.fetch(this.endpoint(path), {
          cache: "no-store",
          credentials: "same-origin",
          ...options,
        });
      } catch {
        throw new LocalReviewError(fallback);
      }
      const contentType = response.headers?.get("content-type") || "";
      if (!contentType.toLocaleLowerCase().startsWith("application/json")) {
        throw new LocalReviewError(fallback, { status: response.status });
      }
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new LocalReviewError(safeErrorMessage(payload, fallback), {
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

    async change(session, action, documentId, targetStatus, expectedStatus) {
      const payload = await this.jsonRequest(
        REVIEW_PATH,
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
            target_status: targetStatus,
            expected_status: expectedStatus,
          }),
        },
        action === "preview" ? "无法预览可信状态" : "无法保存可信状态",
      );
      const change = normalizeChange(payload);
      if (!change) throw new LocalReviewError("知识管家返回了无效的审核结果");
      return change;
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
          // A checked site swap may briefly hide status; keep the wait bounded.
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

  class LocalReviewController {
    constructor({ documentLike, client, notify, reload } = {}) {
      this.document = documentLike || global.document;
      this.client = client || new LocalReviewClient();
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
      this.notify("可信状态已保存；知识管家仍在安全更新网站");
      return false;
    }

    renderControl(documentItem) {
      if (!this.session || !this.document || !documentItem?.id) return null;
      const currentStatus = optionFor(documentItem.status).value;
      const currentOption = optionFor(currentStatus);
      const create = (tag, attributes, ...children) => createElement(
        this.document, tag, attributes, ...children,
      );
      const current = create(
        "div",
        { className: `review-current review-${currentStatus}` },
        create("strong", { text: currentOption.label }),
        create("small", { text: currentOption.detail }),
      );
      const toggle = create("button", {
        className: "review-toggle",
        type: "button",
        text: "调整可信状态",
        "aria-expanded": "false",
      });
      const select = create("select", {
        className: "review-select",
        "aria-label": "选择新的可信状态",
      });
      select.append(create("option", { value: "", text: "请选择可信状态" }));
      for (const option of REVIEW_OPTIONS.filter((item) => item.value !== currentStatus)) {
        select.append(create("option", { value: option.value, text: `${option.label} · ${option.detail}` }));
      }
      const previewButton = create("button", {
        className: "review-preview",
        type: "submit",
        text: "预览变化",
        disabled: "disabled",
      });
      const cancel = create("button", {
        className: "review-cancel",
        type: "button",
        text: "取消",
      });
      const form = create(
        "form",
        { className: "review-form", hidden: true },
        create("label", { text: "调整为" }, select),
        create("small", { text: "可信状态是你的人工判断，不会改写原文或来源证据。" }),
        create("div", { className: "review-actions" }, previewButton, cancel),
      );
      const preview = create("div", {
        className: "review-confirmation",
        hidden: true,
        role: "status",
      });
      const card = create("div", { className: "review-card" }, current, toggle, form, preview);
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
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (busy || !select.value) return;
        setBusy(true);
        previewButton.textContent = "正在预览…";
        try {
          const result = await this.client.change(
            this.session, "preview", documentItem.id, select.value, currentStatus,
          );
          previewedTarget = result.targetStatus;
          const target = optionFor(result.targetStatus);
          const confirm = create("button", {
            className: "review-apply",
            type: "button",
            text: "确认保存",
          });
          preview.replaceChildren(
            create("span", { text: currentOption.label }),
            create("b", { text: "→", "aria-hidden": "true" }),
            create("strong", { text: target.label }),
            create("small", { text: target.detail }),
            confirm,
          );
          preview.hidden = false;
          confirm.addEventListener("click", async () => {
            if (busy || previewedTarget !== select.value) return;
            setBusy(true);
            confirm.disabled = true;
            confirm.textContent = "正在保存并更新网站…";
            try {
              const applied = await this.client.change(
                this.session, "apply", documentItem.id, select.value, currentStatus,
              );
              const reloaded = await this.reloadWhenReady(
                applied,
                `可信状态已更新为“${optionFor(applied.targetStatus).label}”`,
              );
              if (!reloaded) confirm.textContent = "已保存，稍后刷新即可查看";
            } catch (error) {
              confirm.disabled = false;
              confirm.textContent = "确认保存";
              this.notify(error instanceof Error ? error.message : "无法保存可信状态");
            } finally {
              setBusy(false);
            }
          });
        } catch (error) {
          this.notify(error instanceof Error ? error.message : "无法预览可信状态");
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
    controller = new LocalReviewController(options);
    await controller.initialize();
    return controller;
  }

  function renderControl(documentItem) {
    return controller?.renderControl(documentItem) || null;
  }

  function isAvailable() {
    return Boolean(controller?.session);
  }

  global.KnowledgeLocalReview = Object.freeze({
    API_VERSION,
    REVIEW_OPTIONS,
    LocalReviewClient,
    LocalReviewController,
    LocalReviewError,
    isLoopbackLocation,
    isAvailable,
    mount,
    normalizeChange,
    normalizeSession,
    renderControl,
  });
})(typeof window !== "undefined" ? window : globalThis);
