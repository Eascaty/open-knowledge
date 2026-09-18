"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.value = ""; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  focus() { this.focused = true; }
}
const context = vm.createContext({ Node: Element, document: { createElement: tag => new Element(tag) } });
context.window = context;
vm.runInContext(fs.readFileSync("apps/web/src/search-utils.js", "utf8"), context);
vm.runInContext(fs.readFileSync("apps/web/src/app.js", "utf8").replace(/start\(\);\s*$/, ""), context);
vm.runInContext(`
  ui.searchFilters = document.createElement('nav');
  ui.searchInput = document.createElement('input');
  ui.searchResults = document.createElement('ol');
  ui.searchHint = document.createElement('div');
  renderSearchFilters();
  globalThis.testState = state; globalThis.testUi = ui;
`, context);
assert.equal(context.testUi.searchFilters.children.length, 7); // safe before data loads
context.testState.search = {items: [
  {id:"a",type:"document",title:"Java 回收",path:["技术"],tags:["Java"],status:"supported",search_text:"java 垃圾回收"},
  {id:"b",type:"document",title:"Agent",path:["AI"],tags:["Agent"],search_text:"agent"},
]};
vm.runInContext("renderSearchFilters();", context);
const pathSelect = context.testUi.searchFilters.children[3];
pathSelect.value = '["AI"]';
context.testUi.searchInput.value = "java 回收";
pathSelect.events.change();
assert.equal(context.testUi.searchResults.children.length, 0);
assert.match(context.testUi.searchHint.textContent, /清除筛选/);
context.testUi.searchFilters.children[6].events.click();
assert.equal(context.testUi.searchInput.value, "java 回收");
assert.equal(context.testUi.searchResults.children.length, 1);
assert.equal(context.testUi.searchInput.focused, true);
context.testUi.searchInput.value = "";
const tags = context.testUi.searchFilters.children[4];
tags.value = "Agent";
tags.events.change();
assert.equal(context.testUi.searchResults.children.length, 1);
context.testUi.searchFilters.children[6].events.click();
context.testState.searchFilter = "node";
vm.runInContext("renderSearchFilters();", context);
const status = context.testUi.searchFilters.children[5];
status.value = "supported";
status.events.change();
assert.equal(context.testState.searchFilter, "document");
assert.equal(context.testUi.searchResults.children.length, 1);
console.log("Web search UI: 5/5 passed");
