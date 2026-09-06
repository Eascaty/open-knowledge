"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.attributes = {}; this.style = {}; }
  append(...items) { for (const item of items) { item.parentNode = this; this.children.push(item); } }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  click() { return this.events.click?.(); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((item) => item !== this); }
  all(tag) { return [...(this.tagName === tag ? [this] : []), ...this.children.flatMap((item) => item.all(tag))]; }
}

const documentLike = {
  body: new Element("body"),
  createElement: (tag) => new Element(tag),
  execCommand: () => true,
};
const context = { document: documentLike, navigator: {}, location: { href: "http://127.0.0.1:8765/#document=java" } };
vm.runInNewContext(fs.readFileSync("apps/web/src/reading-actions.js", "utf8"), context);
const api = context.KnowledgeReadingActions;

async function testCopiesLinkAndBodyWithoutExposingSource() {
  const copied = [];
  const notices = [];
  const navigatorLike = { clipboard: { writeText: async (value) => copied.push(value) } };
  const toolbar = api.render(
    { title: "Java 基础", content: "## 原文\n\n正文", source: { raw_path: "/private/secret.md" } },
    { documentLike, navigatorLike, locationLike: context.location, notify: (value) => notices.push(value) },
  );
  const buttons = toolbar.all("button");
  assert.equal(buttons.length, 2);
  await buttons[0].click();
  await buttons[1].click();
  assert.equal(copied[0], context.location.href);
  assert.equal(copied[1], "Java 基础\n\n## 原文\n\n正文");
  assert.deepEqual(notices, ["卡片链接已复制", "知识正文已复制"]);
  assert.doesNotMatch(copied[1], /private|secret/);
}

async function testClipboardFallbackIsBounded() {
  const copied = await api.copyText("fallback", { documentLike, navigatorLike: {} });
  assert.equal(copied, true);
  assert.equal(documentLike.body.children.length, 0);
  assert.equal(await api.copyText("no document", { navigatorLike: {}, documentLike: {} }), false);
}

(async () => {
  await testCopiesLinkAndBodyWithoutExposingSource();
  await testClipboardFallbackIsBounded();
  process.stdout.write("Web reading actions: 2/2 passed\n");
})();
