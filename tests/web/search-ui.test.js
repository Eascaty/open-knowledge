"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.value = ""; this.dataset = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  focus() { this.focused = true; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
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
context.testState.documents.set("a", {content:"<script>alert(1)</script> Java 安全阅读"});
context.testUi.searchInput.value = "java";
vm.runInContext("runSearch(ui.searchInput.value);", context);
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
const rendered = flatten(context.testUi.searchResults);
assert.ok(rendered.some(node => node.tagName === "mark" && node.textContent === "Java"));
assert.ok(rendered.some(node => node.textContent === "<script>alert(1)</script> "));
assert.equal(rendered.some(node => node.tagName === "script"), false);
context.document.body = {classList:{add(){},remove(){}}};
context.dispatchEvent = () => {};
context.Event = class {};
context.setTimeout = callback => callback();
vm.runInContext(`
  ui.searchDialog = document.createElement('section');
  ui.searchTrigger = document.createElement('button');
  ui.searchResults.scrollTop = 120;
  navigateToDocument = id => { globalThis.openedId = id; };
`, context);
context.testUi.searchResults.querySelectorAll("button")[0].events.click();
assert.equal(context.openedId, "a");
assert.equal(context.testUi.searchInput.value, "");
assert.equal(context.testState.searchReturn.query, "java");
context.testState.searchStatus = "deprecated";
vm.runInContext("restoreSearchContext();", context);
assert.equal(context.testUi.searchInput.value, "java");
assert.equal(context.testState.searchStatus, "supported");
assert.equal(context.testUi.searchResults.scrollTop, 120);
assert.equal(context.testUi.searchResults.querySelectorAll("button")[0].focused, true);
vm.runInContext("rememberSearchContext({type:'node',id:'node'}); restoreSearchContext();", context);
assert.equal(context.testState.searchReturn, null);
context.testState.search = {items:Array.from({length:65},(_,i)=>({id:`card-${i}`,type:"document",title:`Java ${String(i).padStart(2,"0")}`,search_text:"java",path:[],tags:[]}))};
context.testState.searchStatus = "";
context.testUi.searchInput.value = "java";
vm.runInContext("runSearch(ui.searchInput.value);", context);
let buttons = context.testUi.searchResults.querySelectorAll("button");
assert.equal(buttons.length,31);
buttons[30].events.click();
assert.equal(context.testState.searchPage,1);
buttons = context.testUi.searchResults.querySelectorAll("button");
assert.equal(buttons[0].dataset.documentId,"card-30");
buttons[0].events.click();
vm.runInContext("restoreSearchContext();", context);
assert.equal(context.testState.searchPage,1);
buttons = context.testUi.searchResults.querySelectorAll("button");
buttons[31].events.click();
assert.equal(context.testState.searchPage,2);
assert.equal(context.testUi.searchResults.querySelectorAll("button").length,6);
vm.runInContext("runSearch('java');",context);
assert.equal(context.testState.searchPage,0);
console.log("Web search UI: 10/10 passed");
