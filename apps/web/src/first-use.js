"use strict";
(function exposeFirstUse(global) {
  const SAMPLE = Object.freeze({
    title: "虚构示例：Java 学习笔记",
    content: "# Java 学习笔记（虚构示例）\n\n这份示例用于体验本地导入，不包含真实个人资料。\n\n## 间隔复习\n\n学习一个 Java 概念后，先写下自己的解释，隔一段时间再回顾，并用小例子验证。\n\n## 找回与复用\n\n导入完成后，搜索“间隔复习”，打开知识卡，查看来源，再复制正文用于学习。\n",
  });
  const KEY = "knowledge-first-use-started-v1";
  function mount({ container, data, controller, openDocument, documentLike = global.document, storage }) {
    if (!container) return;
    container.replaceChildren();
    container.hidden = true;
    if (!data || data.site?.visibility !== "private") return;
    const documents = data.documents || [];
    try { if (!storage) storage = global.sessionStorage; } catch { /* Storage may be blocked. */ }
    let started = false;
    try { started = storage?.getItem(KEY) === "yes"; } catch { /* Optional storage. */ }
    const writable = Boolean(controller?.session);
    if (documents.length && (!started || !writable)) return;
    function node(tag, text) {
      const item = documentLike.createElement(tag);
      if (text) item.textContent = text;
      return item;
    }
    function action(label, callback) {
      const button = node("button", label);
      button.type = "button";
      button.addEventListener("click", callback);
      return button;
    }
    function remember() { try { storage?.setItem(KEY, "yes"); } catch { /* Optional storage. */ } }
    container.hidden = false;
    container.setAttribute("aria-labelledby", "first-use-title");
    const title = node("h2", documents.length ? "第一份知识已就绪" : "从第一份资料开始");
    title.id = "first-use-title";
    container.append(title);
    if (documents.length) {
      container.append(node("p", "打开知识卡，核对来源，再试试顶部搜索。以后也可以随时投放新的资料。"));
      container.append(action("打开知识卡", () => {
        openDocument(documents[0].id);
        try { storage?.removeItem(KEY); } catch { /* Optional storage. */ }
        container.hidden = true;
      }));
      return;
    }
    container.append(node("p", writable
      ? "文件或文字 → 自动整理 → 阅读、搜索与复用。资料保存在这台电脑上，默认私密。"
      : "这里还没有知识卡。请从“打开知识库”启动本机知识管家，再投放文件或文字；当前页面仅可阅读。"));
    if (!writable) return;
    const actions = node("div");
    actions.className = "first-use-actions";
    if (!controller.ui.paste.hidden) {
      actions.append(action("用虚构示例试一遍", () => {
        if (controller.previewNote(SAMPLE)) remember();
      }));
    }
    actions.append(action("导入自己的资料", () => { remember(); controller.openDialog(); }));
    container.append(actions);
    const steps = node("ol");
    for (const text of ["选择文件，或预览并确认投入示例文字。", "在投放窗口查看逐条进度；失败说明不会阻止其他资料。", "整理完成后打开知识卡，或在顶部搜索关键词找回。"])
      steps.append(node("li", text));
    container.append(steps);
    container.append(node("p", "示例会作为一份普通私密资料保存；只打开预览不会导入。"));
  }
  global.KnowledgeFirstUse = Object.freeze({ mount, SAMPLE });
})(typeof window !== "undefined" ? window : globalThis);
