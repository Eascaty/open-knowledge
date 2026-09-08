"use strict";

(function exposeReadingActions(global) {
  async function copyText(value, { navigatorLike = global.navigator, documentLike = global.document } = {}) {
    const text = String(value || "");
    try {
      if (navigatorLike?.clipboard?.writeText) {
        await navigatorLike.clipboard.writeText(text);
        return true;
      }
    } catch { /* Try the legacy local fallback below. */ }
    if (!documentLike?.createElement || !documentLike?.body) return false;
    const textarea = documentLike.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.setAttribute("aria-hidden", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    documentLike.body.append(textarea);
    textarea.select?.();
    let copied = false;
    try { copied = Boolean(documentLike.execCommand?.("copy")); } catch { copied = false; }
    textarea.remove();
    return copied;
  }

  function documentUrl(locationLike = global.location) {
    return locationLike?.href || "";
  }

  function documentText(documentItem) {
    return [documentItem.title, documentItem.content].filter((value) => String(value || "").trim()).join("\n\n");
  }

  function button(documentLike, label, handler) {
    const item = documentLike.createElement("button");
    item.className = "reading-action";
    item.textContent = label;
    item.setAttribute("type", "button");
    item.addEventListener("click", handler);
    return item;
  }

  function render(documentItem, {
    documentLike = global.document,
    navigatorLike = global.navigator,
    locationLike = global.location,
    notify = () => {},
  } = {}) {
    const toolbar = documentLike.createElement("div");
    toolbar.className = "reading-actions";
    toolbar.setAttribute("aria-label", "知识卡操作");
    toolbar.append(
      button(documentLike, "复制卡片链接", async () => {
        notify(await copyText(documentUrl(locationLike), { navigatorLike, documentLike })
          ? "卡片链接已复制" : "复制暂时不可用，请手动复制地址栏");
      }),
      button(documentLike, "复制正文", async () => {
        notify(await copyText(documentText(documentItem), { navigatorLike, documentLike })
          ? "知识正文已复制" : "复制暂时不可用，请稍后重试");
      }),
    );
    return toolbar;
  }

  global.KnowledgeReadingActions = Object.freeze({ copyText, documentText, documentUrl, render });
})(typeof window !== "undefined" ? window : globalThis);
