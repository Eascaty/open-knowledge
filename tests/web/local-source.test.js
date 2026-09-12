"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = { URL };
vm.runInNewContext(fs.readFileSync("apps/web/src/local-ingest.js", "utf8"), context);
(async () => {
  let request;
  const client = new context.KnowledgeLocalIngest.LocalManagerClient({
    locationLike: {protocol:"http:",hostname:"127.0.0.1",origin:"http://127.0.0.1:9876"},
    fetchImpl: async (url, options) => { request = {url, options}; return {ok:true,json:async()=>({ok:true,text:"<script>literal</script>"})}; },
  });
  const data = await client.readSource("card-1", {token:"test-session"});
  assert.equal(data.text,"<script>literal</script>");
  assert.equal(request.options.cache,"no-store");
  assert.equal(request.options.headers["X-Knowledge-Session"],"test-session");
  assert.deepEqual(JSON.parse(request.options.body),{document_id:"card-1"});
  client.fetchImpl=async()=>({ok:false,json:async()=>({ok:false,error:{message:"原件摘要已改变"}})});
  await assert.rejects(client.readSource("card-1", {token:"test-session"}),/摘要已改变/);
  console.log("Web local source: 2/2 passed");
})().catch(error=>{ console.error(error);process.exitCode=1; });
