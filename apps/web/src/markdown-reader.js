"use strict";

(function exposeMarkdownReader(global) {
  // Deliberately a small Markdown subset; input never enters an HTML parser.
  function httpLink(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
        ? url.href : null;
    } catch { return null; }
  }

  function node(documentLike, tag, text) {
    const result = documentLike.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function inline(documentLike, parent, value) {
    const tokens = /(`[^`\n]+`|!\[[^\]\n]*\]\([^\s)]+\)|\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
    let offset = 0;
    for (const match of value.matchAll(tokens)) {
      parent.append(documentLike.createTextNode(value.slice(offset, match.index)));
      const token = match[0];
      if (token.startsWith("`")) parent.append(node(documentLike, "code", token.slice(1, -1)));
      else if (token.startsWith("**")) parent.append(node(documentLike, "strong", token.slice(2, -2)));
      else if (token.startsWith("*")) parent.append(node(documentLike, "em", token.slice(1, -1)));
      else {
        const parts = /^(!?)\[([^\]]*)\]\(([^)]+)\)$/.exec(token);
        const href = httpLink(parts[3]);
        if (href) {
          const link = node(documentLike, "a", parts[1] ? `图片：${parts[2] || "查看图片"}` : parts[2]);
          link.setAttribute("href", href);
          link.setAttribute("target", "_blank");
          link.setAttribute("rel", "noopener noreferrer");
          parent.append(link);
        } else parent.append(documentLike.createTextNode(token));
      }
      offset = match.index + token.length;
    }
    parent.append(documentLike.createTextNode(value.slice(offset)));
  }

  const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
  const fence = /^\s{0,3}(`{3,}|~{3,})([^`]*)$/;
  const listItem = /^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/;
  const rule = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }
  function tableSeparator(line) {
    return line.includes("|") && cells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function render(content, { documentLike = global.document } = {}) {
    const body = node(documentLike, "div");
    body.className = "knowledge-body markdown-body";
    const headings = [];
    const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
    const appendInline = (tag, text, parent = body) => {
      const item = node(documentLike, tag);
      inline(documentLike, item, text);
      parent.append(item);
      return item;
    };
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      const opening = fence.exec(line);
      if (opening) {
        const code = [];
        index += 1;
        const closing = new RegExp(`^\\s{0,3}${opening[1][0]}{${opening[1].length},}\\s*$`);
        while (index < lines.length && !closing.test(lines[index])) code.push(lines[index++]);
        if (index < lines.length) index += 1;
        const pre = node(documentLike, "pre");
        pre.setAttribute("tabindex", "0");
        pre.setAttribute("aria-label", opening[2].trim() ? `${opening[2].trim()} 代码` : "代码");
        pre.append(node(documentLike, "code", code.join("\n")));
        body.append(pre);
        continue;
      }
      const title = heading.exec(line);
      if (title) {
        const level = Math.min(6, title[1].length + 1);
        const item = appendInline(`h${level}`, title[2]);
        item.setAttribute("tabindex", "-1");
        headings.push({ level: title[1].length, title: item.textContent, element: item });
        index += 1;
        continue;
      }
      if (rule.test(line)) { body.append(node(documentLike, "hr")); index += 1; continue; }
      if (/^\s*>/.test(line)) {
        const quote = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quote.push(lines[index++].replace(/^\s*>\s?/, ""));
        }
        appendInline("blockquote", quote.join("\n"));
        continue;
      }
      if (line.includes("|") && tableSeparator(lines[index + 1] || "")) {
        const wrapper = node(documentLike, "div");
        wrapper.className = "markdown-table-scroll";
        wrapper.setAttribute("tabindex", "0");
        wrapper.setAttribute("role", "region");
        wrapper.setAttribute("aria-label", "知识表格");
        const table = node(documentLike, "table");
        const header = node(documentLike, "thead");
        const row = node(documentLike, "tr");
        const labels = cells(line);
        for (const label of labels) {
          const th = appendInline("th", label, row);
          th.setAttribute("scope", "col");
        }
        header.append(row);
        table.append(header);
        const rows = node(documentLike, "tbody");
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const values = cells(lines[index++]);
          const tr = node(documentLike, "tr");
          // Keep all cells, including imperfect author-written tables.
          for (let column = 0; column < Math.max(labels.length, values.length); column += 1) {
            appendInline("td", values[column] || "", tr);
          }
          rows.append(tr);
        }
        table.append(rows);
        wrapper.append(table);
        body.append(wrapper);
        continue;
      }
      const firstItem = listItem.exec(line);
      if (firstItem) {
        const ordered = Boolean(firstItem[2]);
        const list = node(documentLike, ordered ? "ol" : "ul");
        if (ordered) list.setAttribute("start", firstItem[2]);
        while (index < lines.length) {
          const item = listItem.exec(lines[index]);
          if (!item || Boolean(item[2]) !== ordered || rule.test(lines[index])) break;
          appendInline("li", item[3], list);
          index += 1;
        }
        body.append(list);
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim()) {
        const next = lines[index];
        if (heading.test(next) || fence.test(next) || listItem.test(next) || rule.test(next)
          || /^\s*>/.test(next) || (next.includes("|") && tableSeparator(lines[index + 1] || ""))) break;
        paragraph.push(next);
        index += 1;
      }
      appendInline("p", paragraph.join("\n"));
    }
    return { body, headings };
  }

  function markdownFile(documentItem) {
    const title = String(documentItem.title || "知识卡");
    const filename = (title.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, "_")
      .replace(/^\.+|[. ]+$/g, "").slice(0, 80) || "知识卡") + ".md";
    // Export the displayed card body verbatim, not a reconstructed source document.
    return { filename, content: String(documentItem.content || "") };
  }

  function reader(documentItem, { documentLike = global.document, notify = () => {} } = {}) {
    const section = node(documentLike, "section");
    section.className = "section-block markdown-reader";
    const toolbar = node(documentLike, "div");
    toolbar.className = "section-title";
    toolbar.append(node(documentLike, "h2", "知识正文"));
    const download = node(documentLike, "button", "下载 Markdown");
    download.className = "markdown-download";
    download.setAttribute("type", "button");
    download.addEventListener("click", () => {
      let url;
      try {
        const file = markdownFile(documentItem);
        url = global.URL.createObjectURL(new global.Blob([file.content], { type: "text/markdown;charset=utf-8" }));
        const anchor = node(documentLike, "a");
        anchor.setAttribute("href", url);
        anchor.setAttribute("download", file.filename);
        anchor.hidden = true;
        section.append(anchor);
        try { anchor.click(); } finally { anchor.remove(); }
      } catch { notify("下载暂时不可用，请稍后重试"); }
      finally { if (url) global.setTimeout(() => global.URL.revokeObjectURL(url), 1000); }
    });
    toolbar.append(download);
    section.append(toolbar);
    const rendered = render(documentItem.content, { documentLike });
    if (rendered.headings.length >= 2) {
      const outline = node(documentLike, "details");
      outline.className = "markdown-outline";
      outline.append(node(documentLike, "summary", `本文目录 · ${rendered.headings.length} 节`));
      const navigation = node(documentLike, "nav");
      navigation.setAttribute("aria-label", "本文目录");
      for (const headingItem of rendered.headings) {
        const button = node(documentLike, "button", headingItem.title);
        button.setAttribute("type", "button");
        button.className = `markdown-outline-level-${headingItem.level}`;
        button.addEventListener("click", () => {
          headingItem.element.scrollIntoView({ block: "start" });
          headingItem.element.focus({ preventScroll: true });
        });
        navigation.append(button);
      }
      outline.append(navigation);
      section.append(outline);
    }
    section.append(rendered.body);
    return section;
  }

  global.KnowledgeMarkdownReader = Object.freeze({ render, reader, markdownFile });
})(typeof window !== "undefined" ? window : globalThis);
