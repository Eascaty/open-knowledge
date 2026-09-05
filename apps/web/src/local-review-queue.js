"use strict";

(function exposeLocalReviewQueue(global) {
  const REVIEW_STATUSES = Object.freeze([
    { value: "unverified", label: "待验证", priority: 2 },
    { value: "personal", label: "个人认知", priority: 4 },
    { value: "supported", label: "有证据支持", priority: 5 },
    { value: "verified-by-practice", label: "实践验证", priority: 6 },
    { value: "contradicted", label: "存在争议", priority: 0 },
    { value: "deprecated", label: "已过时", priority: 1 },
  ]);
  const ATTENTION_STATUSES = new Set(["unverified", "contradicted", "deprecated"]);
  const INITIAL_VISIBLE = 60;

  function statusOption(value) {
    return REVIEW_STATUSES.find((item) => item.value === value) || REVIEW_STATUSES[0];
  }

  function normalizedStatus(documentItem) {
    return statusOption(documentItem?.status).value;
  }

  function reviewCounts(documents) {
    const counts = Object.fromEntries(REVIEW_STATUSES.map((item) => [item.value, 0]));
    for (const documentItem of documents || []) {
      if (!documentItem || typeof documentItem.id !== "string") continue;
      counts[normalizedStatus(documentItem)] += 1;
    }
    counts.attention = [...ATTENTION_STATUSES].reduce(
      (total, status) => total + counts[status], 0,
    );
    counts.total = REVIEW_STATUSES.reduce((total, item) => total + counts[item.value], 0);
    return counts;
  }

  function compareDocuments(left, right) {
    const byPriority = statusOption(normalizedStatus(left)).priority
      - statusOption(normalizedStatus(right)).priority;
    if (byPriority) return byPriority;
    const byDate = String(left.updated_at || "").localeCompare(String(right.updated_at || ""));
    return byDate || String(left.title || "").localeCompare(String(right.title || ""), "zh-CN");
  }

  function selectReviewDocuments(documents, filter = "attention") {
    return (documents || [])
      .filter((item) => item && typeof item.id === "string")
      .filter((item) => {
        const status = normalizedStatus(item);
        if (filter === "all") return true;
        if (filter === "attention") return ATTENTION_STATUSES.has(status);
        return status === filter;
      })
      .sort(compareDocuments);
  }

  function createElement(documentLike, tag, attributes = {}, ...children) {
    const element = documentLike.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "className") element.className = value;
      else if (key === "text") element.textContent = value;
      else if (key === "hidden") element.hidden = Boolean(value);
      else element.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child) element.append(child);
    return element;
  }

  function formatDate(value) {
    if (!value) return "未记录时间";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
    }).format(date);
  }

  class LocalReviewQueueController {
    constructor({ documentLike, windowLike, documents, openDocument, notify, ui } = {}) {
      this.document = documentLike || global.document;
      this.window = windowLike || global;
      this.documents = Array.isArray(documents) ? documents : [];
      this.openDocument = typeof openDocument === "function" ? openDocument : () => {};
      this.notify = typeof notify === "function" ? notify : () => {};
      this.ui = ui || null;
      this.filter = "attention";
      this.visible = INITIAL_VISIBLE;
      this.lastFocused = null;
    }

    queryUi() {
      if (this.ui) return true;
      if (!this.document?.getElementById) return false;
      this.ui = {
        trigger: this.document.getElementById("review-queue-trigger"),
        count: this.document.getElementById("review-queue-count"),
        dialog: this.document.getElementById("review-queue-dialog"),
        summary: this.document.getElementById("review-queue-summary"),
        filters: this.document.getElementById("review-queue-filters"),
        list: this.document.getElementById("review-queue-list"),
        empty: this.document.getElementById("review-queue-empty"),
        more: this.document.getElementById("review-queue-more"),
      };
      return Object.values(this.ui).every(Boolean);
    }

    initialize() {
      if (!global.KnowledgeLocalReview?.isAvailable?.() || !this.queryUi()) return false;
      this.bind();
      this.render();
      this.ui.trigger.hidden = false;
      return true;
    }

    bind() {
      this.ui.trigger.addEventListener("click", () => this.open());
      this.ui.more.addEventListener("click", () => {
        this.visible += INITIAL_VISIBLE;
        this.renderList();
      });
      for (const closer of this.document.querySelectorAll("[data-close-review-queue]")) {
        closer.addEventListener("click", () => this.close());
      }
      this.window.addEventListener("knowledge:close-review-queue", () => this.close(false));
      this.document.addEventListener("keydown", (event) => this.handleKeydown(event), true);
    }

    open() {
      this.window.dispatchEvent(new Event("knowledge:close-search"));
      this.window.dispatchEvent(new Event("knowledge:close-ingest"));
      this.lastFocused = this.document.activeElement;
      this.ui.dialog.hidden = false;
      this.document.body.classList.add("review-queue-open");
      this.render();
      this.window.setTimeout(() => {
        const next = this.ui.dialog.querySelector("button:not([disabled])");
        next?.focus();
      }, 0);
    }

    close(restoreFocus = true) {
      if (this.ui.dialog.hidden) return;
      this.ui.dialog.hidden = true;
      this.document.body.classList.remove("review-queue-open");
      if (restoreFocus && this.lastFocused?.focus) this.lastFocused.focus();
    }

    handleKeydown(event) {
      if (this.ui.dialog.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        this.close();
        return;
      }
      if (event.key !== "Tab" || !this.ui.dialog.querySelectorAll) return;
      const items = [...this.ui.dialog.querySelectorAll(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled])",
      )].filter((item) => !item.hidden && !item.closest?.("[hidden]"));
      if (!items.length) return;
      const first = items[0];
      const last = items.at(-1);
      if (event.shiftKey && this.document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && this.document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    setFilter(filter) {
      this.filter = filter;
      this.visible = INITIAL_VISIBLE;
      this.renderFilters();
      this.renderList();
    }

    render() {
      const counts = reviewCounts(this.documents);
      this.ui.count.textContent = String(counts.attention);
      this.ui.trigger.classList.toggle("complete", counts.attention === 0);
      this.ui.trigger.setAttribute(
        "aria-label",
        counts.attention ? `有 ${counts.attention} 条知识需要复核` : "知识复核已完成",
      );
      this.ui.summary.textContent = counts.attention
        ? `${counts.attention} 条需要处理，共 ${counts.total} 条知识`
        : `当前 ${counts.total} 条知识均已完成人工判断`;
      this.renderFilters(counts);
      this.renderList();
    }

    renderFilters(existingCounts = null) {
      const counts = existingCounts || reviewCounts(this.documents);
      const choices = [
        { value: "attention", label: "需处理", count: counts.attention },
        ...REVIEW_STATUSES.map((item) => ({ ...item, count: counts[item.value] })),
        { value: "all", label: "全部", count: counts.total },
      ];
      this.ui.filters.replaceChildren();
      for (const choice of choices) {
        const button = createElement(
          this.document,
          "button",
          {
            className: "review-queue-filter",
            type: "button",
            "aria-pressed": String(this.filter === choice.value),
          },
          createElement(this.document, "span", { text: choice.label }),
          createElement(this.document, "strong", { text: String(choice.count) }),
        );
        button.addEventListener("click", () => this.setFilter(choice.value));
        this.ui.filters.append(button);
      }
    }

    renderList() {
      const selected = selectReviewDocuments(this.documents, this.filter);
      this.ui.list.replaceChildren();
      this.ui.empty.hidden = selected.length > 0;
      this.ui.empty.textContent = this.filter === "attention"
        ? "很好，目前没有待验证、存在争议或已过时的知识。"
        : "这个状态下暂时没有知识。";
      for (const documentItem of selected.slice(0, this.visible)) {
        const status = statusOption(normalizedStatus(documentItem));
        const path = Array.isArray(documentItem.path) ? documentItem.path.join(" / ") : "待归类";
        const button = createElement(
          this.document,
          "button",
          { className: "review-queue-item", type: "button" },
          createElement(
            this.document,
            "span",
            { className: "review-queue-item-main" },
            createElement(this.document, "strong", { text: documentItem.title || "未命名知识" }),
            createElement(this.document, "small", { text: path || "待归类" }),
          ),
          createElement(
            this.document,
            "span",
            { className: `review-queue-item-status review-${status.value}` },
            createElement(this.document, "b", { text: status.label }),
            createElement(this.document, "time", { text: formatDate(documentItem.updated_at) }),
          ),
          createElement(this.document, "span", {
            className: "review-queue-arrow", text: "›", "aria-hidden": "true",
          }),
        );
        button.addEventListener("click", () => {
          this.close(false);
          this.openDocument(documentItem.id);
        });
        const listItem = createElement(this.document, "li");
        listItem.append(button);
        this.ui.list.append(listItem);
      }
      this.ui.more.hidden = selected.length <= this.visible;
      const remaining = Math.max(0, selected.length - this.visible);
      this.ui.more.textContent = `再显示 ${Math.min(INITIAL_VISIBLE, remaining)} 条`;
    }
  }

  let controller = null;

  function mount(options = {}) {
    if (controller) return controller;
    controller = new LocalReviewQueueController(options);
    return controller.initialize() ? controller : null;
  }

  global.KnowledgeLocalReviewQueue = Object.freeze({
    ATTENTION_STATUSES,
    LocalReviewQueueController,
    REVIEW_STATUSES,
    mount,
    reviewCounts,
    selectReviewDocuments,
  });
})(typeof window !== "undefined" ? window : globalThis);
