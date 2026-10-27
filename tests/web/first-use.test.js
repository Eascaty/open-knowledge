"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.hidden = false; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  addEventListener(name, callback) { this.events[name] = callback; }
  click() { return this.events.click?.(); }
  all(tag) { return [...(this.tagName === tag ? [this] : []), ...this.children.flatMap(item => item.all(tag))]; }
}
const context = { document: { createElement: tag => new Element(tag) } };
vm.runInNewContext(fs.readFileSync("apps/web/src/first-use.js", "utf8"), context);
vm.runInNewContext(fs.readFileSync("apps/web/src/local-ingest.js", "utf8"), context);
const mount = context.KnowledgeFirstUse.mount;
const privateData = { site: { visibility: "private" }, documents: [] };
function state() {
  const store = new Map();
  return { container: new Element("section"), data: privateData,
    storage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) } };
}
{
  for (const visibility of ["public", "private"]) {
    const options = state();
    options.data = { ...privateData, site: { visibility } };
    mount(options);
    assert.equal(options.container.all("button").length, 0);
    assert.equal(options.container.hidden, visibility === "public");
  }
}
{
  const options = state();
  let opened = 0, preview = null;
  options.controller = { session: {}, ui: { paste: { hidden: false } }, openDialog: () => opened++, previewNote: sample => { preview = sample; return true; } };
  mount(options);
  assert.equal(preview, null);
  options.container.all("button")[0].click();
  assert.match(preview.content, /虚构示例/);
  assert.equal(opened, 0);
  options.container.all("button")[1].click();
  assert.equal(opened, 1);
  let selected;
  options.data = { ...privateData, documents: [{ id: "sample" }] };
  options.openDocument = id => { selected = id; };
  mount(options);
  options.container.all("button")[0].click();
  assert.equal(selected, "sample");
  assert.equal(options.container.hidden, true);
  mount(options);
  assert.equal(options.container.hidden, true);
}
{
  const options = state();
  options.storage = { getItem() { throw Error(); }, setItem() { throw Error(); } };
  options.controller = { session: {}, ui: { paste: { hidden: true } }, openDialog() {} };
  mount(options);
  assert.equal(options.container.all("button").length, 1);
  options.container.all("button")[0].click();
}
{
  const preview = context.KnowledgeLocalIngest.LocalIngestController.prototype.previewNote;
  const draft = { session: {}, ui: { paste: { hidden: false }, pasteTitle: { value: "我的草稿" }, pasteContent: { value: "原有文字" } }, openDialog() {}, notify() {} };
  assert.equal(preview.call(draft, context.KnowledgeFirstUse.SAMPLE), false);
  assert.equal(draft.ui.pasteContent.value, "原有文字");
  draft.ui.pasteTitle.value = "";
  draft.ui.pasteContent.value = "";
  draft.ui.pasteContent.focus = () => {};
  draft.updatePasteState = () => {};
  draft.window = { setTimeout: callback => callback() };
  assert.equal(preview.call(draft, context.KnowledgeFirstUse.SAMPLE), true);
  assert.match(draft.ui.pasteContent.value, /虚构示例/);
  draft.session = null;
  assert.equal(preview.call(draft, context.KnowledgeFirstUse.SAMPLE), false);
}
console.log("Web first use: 4/4 passed");
