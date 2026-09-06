"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag, text = "") {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.events = {};
    this.text = text;
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map((item) => item.textContent).join(""); }
  append(...items) { this.children.push(...items); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; }
  click() { this.events.click?.(); }
  remove() {}
  all(tag) {
    return [...(this.tagName === tag ? [this] : []), ...this.children.flatMap((item) => item.all(tag))];
  }
}
const documentLike = {
  createElement: (tag) => new Element(tag),
  createTextNode: (text) => new Element("#text", text),
};
const context = { URL, Blob, setTimeout, document: documentLike };
vm.runInNewContext(fs.readFileSync("apps/web/src/markdown-reader.js", "utf8"), context);
const api = context.KnowledgeMarkdownReader;

function testFormattedKnowledgeAndOutline() {
  const content = "## Java\n\n**关键**与 `value`\n\n- 第一项\n- 第二项\n\n3. 有序\n4. 后续\n\n> 引用\n\n### 示例\n\n```java\nList<String> list;\n```\n\n| 类型 | 说明 |\n| --- | --- |\n| JVM | 内存 |";
  const { body, headings } = api.render(content);
  assert.equal(headings.length, 2);
  assert.equal(body.all("strong")[0].textContent, "关键");
  assert.equal(body.all("ul")[0].children.length, 2);
  assert.equal(body.all("ol")[0].attributes.start, "3");
  assert.equal(body.all("pre")[0].textContent, "List<String> list;");
  assert.equal(body.all("td")[1].textContent, "内存");
  const reader = api.reader({ title: "Java", content });
  const outlineButton = reader.all("nav")[0].all("button")[1];
  outlineButton.click();
  const target = reader.all("h4")[0];
  assert.equal(target.scrolled, true);
  assert.equal(target.focused, true);
}

function testUntrustedContentNeverExecutesOrLoadsImages() {
  const { body } = api.render('<script>alert(1)</script>\n\n[bad](javascript:alert) ![tracker](https://example.com/image) [safe](https://example.com/doc)\n\n[credentials](https://user:pass@example.com/) [data](data:text/html,evil)\n\n```html\n<img src=x onerror=alert(1)>\n```');
  assert.equal(body.all("script").length, 0);
  assert.equal(body.all("img").length, 0);
  assert.equal(body.all("a").length, 2);
  for (const link of body.all("a")) {
    assert.match(link.attributes.href, /^https:\/\//);
    assert.equal(link.attributes.rel, "noopener noreferrer");
  }
  assert.match(body.textContent, /<script>alert\(1\)<\/script>/);
  assert.match(body.textContent, /javascript:alert/);
  assert.equal(body.all("pre")[0].textContent, "<img src=x onerror=alert(1)>");
}

function testFencesAndFallbackPreserveContent() {
  const content = "````text\n## not a heading\n```\nstill code\n````\n\n~~~\nunclosed <tag>\n";
  const { body, headings } = api.render(content);
  assert.equal(headings.length, 0);
  assert.equal(body.all("pre").length, 2);
  assert.match(body.all("pre")[0].textContent, /```\nstill code/);
  assert.match(body.all("pre")[1].textContent, /unclosed <tag>/);
  assert.match(api.render("<div>未支持的 HTML</div>").body.textContent, /<div>/);
}

function testDownloadKeepsDisplayedMarkdownVerbatim() {
  const content = "## 原文\r\n\n```java\nint x = 1;\n```\n";
  const file = api.markdownFile({ title: "../Java:笔记/测试", content, source: { raw_path: "private" } });
  assert.equal(file.content, content);
  assert.match(file.filename, /\.md$/);
  assert.doesNotMatch(file.filename, /[/:\\]/);
  assert.doesNotMatch(file.content, /private/);
  assert.equal(api.markdownFile({ title: "...", content: "" }).filename, "知识卡.md");
}

[testFormattedKnowledgeAndOutline, testUntrustedContentNeverExecutesOrLoadsImages,
  testFencesAndFallbackPreserveContent, testDownloadKeepsDisplayedMarkdownVerbatim].forEach((test) => test());
process.stdout.write("Web Markdown reader: 4/4 passed\n");
