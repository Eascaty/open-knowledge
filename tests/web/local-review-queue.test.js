"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("apps/web/src/local-review-queue.js", "utf8");

function loadModule(available = true) {
  const listeners = new Map();
  const context = {
    Date,
    Event,
    Intl,
    URL,
    console,
    KnowledgeLocalReview: { isAvailable: () => available },
    addEventListener(name, callback) { listeners.set(name, callback); },
    dispatchEvent(event) { listeners.get(event.type)?.(event); },
    setTimeout(callback) { callback(); },
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "local-review-queue.js" });
  return { api: context.KnowledgeLocalReviewQueue, windowLike: context };
}

class ClassListStub {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, enabled) {
    if (enabled) this.values.add(value);
    else this.values.delete(value);
  }
  contains(value) { return this.values.has(value); }
}

class ElementStub {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.classList = new ClassListStub();
    this.className = "";
    this.hidden = false;
    this.textContent = "";
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  focus() { this.focused = true; }
  querySelector() { return this.children.find((item) => item.tagName === "BUTTON") || null; }
  async emit(name, event = {}) {
    return this.listeners.get(name)?.({
      preventDefault() {}, stopImmediatePropagation() {}, ...event,
    });
  }
}

class DocumentStub {
  constructor(closers = []) {
    this.body = { classList: new ClassListStub() };
    this.activeElement = new ElementStub("button");
    this.closers = closers;
    this.listeners = new Map();
  }
  createElement(tag) { return new ElementStub(tag); }
  querySelectorAll(selector) {
    return selector === "[data-close-review-queue]" ? this.closers : [];
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
}

function documentItem(id, status, updatedAt, title = id) {
  return {
    id,
    status,
    title,
    updated_at: updatedAt,
    path: ["技术", "程序员"],
  };
}

function createUi() {
  const trigger = new ElementStub("button");
  trigger.hidden = true;
  const dialog = new ElementStub("section");
  dialog.hidden = true;
  return {
    trigger,
    count: new ElementStub("strong"),
    dialog,
    summary: new ElementStub("p"),
    filters: new ElementStub("nav"),
    list: new ElementStub("ol"),
    scroll: new ElementStub("div"),
    empty: new ElementStub("p"),
    more: new ElementStub("button"),
  };
}

function testCountsAndPriorityAreDeterministic() {
  const { api } = loadModule();
  const documents = [
    documentItem("verified", "verified-by-practice", "2026-08-01"),
    documentItem("old-unverified", "unverified", "2024-01-01"),
    documentItem("new-unverified", "unverified", "2025-01-01"),
    documentItem("contradicted", "contradicted", "2026-01-01"),
    documentItem("deprecated", "deprecated", "2023-01-01"),
    documentItem("unknown", "invented", "2022-01-01"),
    { title: "missing id", status: "unverified" },
  ];
  const counts = api.reviewCounts(documents);
  assert.equal(counts.total, 6);
  assert.equal(counts.attention, 5);
  assert.equal(counts.unverified, 3);
  assert.deepEqual(
    [...api.selectReviewDocuments(documents, "attention")].map((item) => item.id),
    ["contradicted", "deprecated", "unknown", "old-unverified", "new-unverified"],
  );
  assert.deepEqual(
    [...api.selectReviewDocuments(documents, "verified-by-practice")].map((item) => item.id),
    ["verified"],
  );
}

function testPublicOrUnavailableManagerStaysHidden() {
  const { api, windowLike } = loadModule(false);
  const ui = createUi();
  const controller = new api.LocalReviewQueueController({
    documentLike: new DocumentStub(), windowLike, ui,
  });
  assert.equal(controller.initialize(), false);
  assert.equal(ui.trigger.hidden, true);
}

async function testLocalQueueFiltersAndOpensKnowledge() {
  const { api, windowLike } = loadModule(true);
  const closer = new ElementStub("button");
  const documentLike = new DocumentStub([closer]);
  const ui = createUi();
  const opened = [];
  const controller = new api.LocalReviewQueueController({
    documentLike,
    windowLike,
    ui,
    documents: [
      documentItem("supported", "supported", "2025-01-01", "有证据的知识"),
      documentItem("debate", "contradicted", "2026-01-01", "存在争议的知识"),
      documentItem("todo", "unverified", "2024-01-01", "待验证知识"),
    ],
    openDocument: (id) => opened.push(id),
  });
  assert.equal(controller.initialize(), true);
  assert.equal(ui.trigger.hidden, false);
  assert.equal(ui.count.textContent, "2");
  assert.match(ui.summary.textContent, /2 条需要处理/);
  assert.equal(ui.list.children.length, 2);

  await ui.trigger.emit("click");
  assert.equal(ui.dialog.hidden, false);
  assert.equal(documentLike.body.classList.contains("review-queue-open"), true);

  const supportedFilter = ui.filters.children.find(
    (item) => item.children[0].textContent === "有证据支持",
  );
  await supportedFilter.emit("click");
  assert.equal(ui.list.children.length, 1);
  const resultButton = ui.list.children[0].children[0];
  assert.equal(resultButton.children[0].children[0].textContent, "有证据的知识");
  await resultButton.emit("click");
  assert.deepEqual(opened, ["supported"]);
  assert.equal(ui.dialog.hidden, true);
}

async function testLargeQueueRendersInBoundedPages() {
  const { api, windowLike } = loadModule(true);
  const ui = createUi();
  const controller = new api.LocalReviewQueueController({
    documentLike: new DocumentStub(),
    windowLike,
    ui,
    documents: Array.from({ length: 61 }, (_, index) => documentItem(
      `doc-${index}`, "unverified", `2025-01-${String((index % 28) + 1).padStart(2, "0")}`,
    )),
  });
  assert.equal(controller.initialize(), true);
  assert.equal(ui.list.children.length, 60);
  assert.equal(ui.more.hidden, false);
  assert.equal(ui.more.textContent, "再显示 1 条");
  await ui.more.emit("click");
  assert.equal(ui.list.children.length, 61);
  assert.equal(ui.more.hidden, true);
}

async function testReturnRestoresFilterExpandedListScrollAndFocus() {
  const { api, windowLike } = loadModule(true);
  const ui = createUi();
  const controller = new api.LocalReviewQueueController({
    documentLike: new DocumentStub(), windowLike, ui,
    documents: Array.from({length: 65}, (_, i) => documentItem(
      `doc-${String(i).padStart(2, "0")}`, "supported", "2025-01-01")),
  });
  assert.equal(controller.initialize(), true);
  assert.equal(controller.returnButton("doc-64"), null);
  controller.setFilter("supported");
  controller.open();
  await ui.more.emit("click");
  ui.scroll.scrollTop = 900;
  await ui.list.children[64].children[0].emit("click");
  assert.equal(ui.dialog.hidden,true);
  assert.equal(controller.returnButton("unrelated"),null);
  const back = controller.returnButton("doc-64");
  controller.setFilter("attention");
  ui.scroll.scrollTop = 0;
  await back.emit("click");
  assert.equal(controller.filter,"supported");
  assert.equal(ui.dialog.hidden,false);
  assert.equal(ui.list.children.length,65);
  assert.equal(ui.scroll.scrollTop,900);
  assert.equal(ui.list.children[64].children[0].focused,true);
  const fresh = new api.LocalReviewQueueController({documentLike:new DocumentStub(),windowLike,ui:createUi()});
  assert.equal(fresh.returnButton("doc-64"),null);
}

async function testDeferralIsReversibleAndDoesNotChangeReviewState() {
  const {api,windowLike} = loadModule();
  const ui=createUi();
  const documents=[documentItem("todo","unverified","2025-01-01")];
  const before=JSON.stringify(documents);
  const controller=new api.LocalReviewQueueController({documentLike:new DocumentStub(),windowLike,ui,documents});
  controller.initialize();
  await ui.list.children[0].children[1].emit("click");
  assert.equal(ui.list.children.length,0);
  assert.match(ui.empty.textContent,/尚未完成复核/);
  assert.equal(ui.count.textContent,"1");
  assert.match(ui.summary.textContent,/刷新后恢复/);
  controller.setFilter("deferred");
  assert.equal(ui.list.children.length,1);
  controller.setFilter("all");
  assert.equal(ui.list.children.length,1);
  controller.setFilter("deferred");
  await ui.list.children[0].children[1].emit("click");
  assert.equal(ui.list.children.length,0);
  controller.setFilter("attention");
  assert.equal(ui.list.children.length,1);
  assert.equal(JSON.stringify(documents),before);
  await ui.list.children[0].children[1].emit("click");
  const fresh=new api.LocalReviewQueueController({documentLike:new DocumentStub(),windowLike,ui:createUi(),documents});
  fresh.initialize();
  assert.equal(fresh.ui.list.children.length,1);
}

Promise.resolve()
  .then(testCountsAndPriorityAreDeterministic)
  .then(testPublicOrUnavailableManagerStaysHidden)
  .then(testLocalQueueFiltersAndOpensKnowledge)
  .then(testLargeQueueRendersInBoundedPages)
  .then(testReturnRestoresFilterExpandedListScrollAndFocus)
  .then(testDeferralIsReversibleAndDoesNotChangeReviewState)
  .then(() => process.stdout.write("Web local review queue: 6/6 passed\n"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
