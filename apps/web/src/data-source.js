"use strict";

(function exposeKnowledgeDataSources(global) {
  async function fetchJson(path) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${path} 返回 ${response.status}`);
    return response.json();
  }

  async function fetchStaticJson(path) {
    if (typeof global.DecompressionStream === "function") {
      try {
        const response = await fetch(`${path}.gz`, {
          credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/gzip, application/octet-stream" },
        });
        if (!response.ok) throw new Error("Compressed data unavailable");
        const bytes = new Uint8Array(await response.arrayBuffer());
        // Some hosts apply Content-Encoding and the browser has already decoded it.
        const stream = new Blob([bytes]).stream();
        const decoded = bytes[0] === 0x1f && bytes[1] === 0x8b
          ? stream.pipeThrough(new global.DecompressionStream("gzip")) : stream;
        return await new Response(decoded).json();
      } catch (_) {
        // Old bundles, offline public caches and incompatible hosts retain JSON fallback.
      }
    }
    return fetchJson(path);
  }

  class StaticBundleDataSource {
    constructor(basePath = ".") {
      this.basePath = basePath.replace(/\/$/, "");
    }

    async loadWorkspace() {
      const [data, search, graph] = await Promise.all([
        fetchStaticJson(`${this.basePath}/data/site-data.json`),
        fetchStaticJson(`${this.basePath}/data/search-index.json`),
        fetchStaticJson(`${this.basePath}/data/graph.json`),
      ]);
      return { data, search, graph };
    }
  }

  class ApiKnowledgeDataSource {
    constructor(basePath = "./api/v1") {
      this.basePath = basePath.replace(/\/$/, "");
    }

    async request(path, parameters = {}) {
      const query = new URLSearchParams(parameters);
      const queryText = query.toString();
      const suffix = queryText ? `?${queryText}` : "";
      const payload = await fetchJson(`${this.basePath}${path}${suffix}`);
      if (!payload || payload.api_version !== "v1" || !("data" in payload)) {
        throw new Error(`${path} 未返回 API v1 数据`);
      }
      return payload.data;
    }

    health() {
      return this.request("/health");
    }

    taxonomy() {
      return this.request("/taxonomy");
    }

    listDocuments({ query = "", page = 0, size = 20 } = {}) {
      return this.request("/documents", { query, page, size });
    }

    document(id) {
      return this.request(`/documents/${encodeURIComponent(id)}`);
    }

    search(query, { page = 0, size = 20 } = {}) {
      return this.request("/search", { q: query, page, size });
    }

    async loadWorkspace() {
      const [roots, summaries] = await Promise.all([
        this.taxonomy(),
        this.loadAllSummaries(),
      ]);
      const details = await Promise.all(
        summaries.map((summary) => this.document(summary.id)),
      );
      return workspaceFromApi(roots, details);
    }

    async loadAllSummaries() {
      const items = [];
      let page = 0;
      while (true) {
        const result = await this.listDocuments({ page, size: 100 });
        items.push(...result.items);
        if (items.length >= result.total || result.items.length === 0) return items;
        page += 1;
      }
    }
  }

  function flattenTaxonomy(roots) {
    const nodes = [];
    function visit(node, inheritedPath = []) {
      const path = Array.isArray(node.path) && node.path.length
        ? node.path
        : [...inheritedPath, node.name];
      const children = Array.isArray(node.children) ? node.children : [];
      nodes.push({
        id: node.id,
        parent_id: node.parent_id ?? null,
        name: node.name,
        level: Math.max(0, path.length - 1),
        path,
        locked: Boolean(node.locked),
        summary: "",
        visibility: "public",
        children: children.map((child) => child.id),
        direct_document_count: 0,
        document_count: 0,
      });
      for (const child of children) visit(child, path);
    }
    for (const root of roots) visit(root);
    return nodes;
  }

  function attachDocumentCounts(nodes, documents) {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    for (const documentItem of documents) {
      const node = byId.get(documentItem.node_id);
      if (node) node.direct_document_count += 1;
    }
    function count(nodeId) {
      const node = byId.get(nodeId);
      if (!node) return 0;
      node.document_count = node.direct_document_count
        + node.children.reduce((total, childId) => total + count(childId), 0);
      return node.document_count;
    }
    for (const node of nodes.filter((item) => item.parent_id === null)) count(node.id);
  }

  function workspaceFromApi(roots, details) {
    if (!Array.isArray(roots) || roots.length !== 1) {
      throw new Error("API v1 taxonomy 必须返回唯一根节点");
    }
    const nodes = flattenTaxonomy(roots);
    const relations = [];
    const documents = details.map((item) => {
      for (const relation of item.relations || []) {
        relations.push({
          id: relation.id,
          from_node_id: relation.from_node_id,
          to_node_id: relation.to_node_id,
          type: relation.relation_type,
          label: relation.label,
          document_id: item.id,
          confidence: relation.confidence,
          visibility: "public",
          evidence_ids: [],
        });
      }
      return {
        id: item.id,
        source_id: "",
        title: item.title,
        summary: item.summary || "",
        content: item.body || "",
        key_points: [],
        tags: item.tags || [],
        node_id: item.node_id,
        path: item.path || [],
        visibility: "public",
        status: "unverified",
        source: { ...(item.source || {}), origin: "" },
        evidence: [],
        updated_at: item.updated_at || "",
      };
    });
    attachDocumentCounts(nodes, documents);
    const timestamps = documents
      .map((item) => item.updated_at)
      .filter(Boolean)
      .sort();
    const generatedAt = timestamps[timestamps.length - 1] || "";
    const data = {
      schema_version: 1,
      generated_at: generatedAt,
      site: {
        title: document.title || "Personal Knowledge OS",
        description: "通过只读 API v1 加载的个人知识网络",
        language: document.documentElement.lang || "zh-CN",
        visibility: "public",
      },
      root: roots[0].id,
      nodes,
      documents,
      relations,
      stats: {
        node_count: nodes.length,
        document_count: documents.length,
        relation_count: relations.length,
      },
    };
    const search = {
      schema_version: 1,
      generated_at: generatedAt,
      items: [
        ...nodes.map((node) => ({
          id: node.id,
          type: "node",
          node_id: node.id,
          title: node.name,
          path: node.path,
          summary: node.summary,
          tags: [],
          status: "",
          updated_at: "",
          search_text: [...node.path, node.summary].join(" ").toLocaleLowerCase(),
        })),
        ...documents.map((item) => ({
          id: item.id,
          type: "document",
          node_id: item.node_id,
          title: item.title,
          path: item.path,
          summary: item.summary,
          tags: item.tags,
          status: item.status || "unverified",
          updated_at: item.updated_at,
          search_text: [item.title, item.summary, item.content, ...item.tags, ...item.path]
            .join(" ")
            .toLocaleLowerCase(),
        })),
      ],
    };
    const graph = {
      schema_version: 1,
      generated_at: generatedAt,
      nodes: nodes.map((node) => ({
        id: node.id,
        name: node.name,
        path: node.path,
        level: node.level,
        document_count: node.document_count,
      })),
      tree_edges: nodes
        .filter((node) => node.parent_id !== null)
        .map((node) => ({
          id: `tree:${node.parent_id}:${node.id}`,
          from: node.parent_id,
          to: node.id,
          type: "parent_of",
        })),
      relation_edges: relations.map((relation) => ({
        id: relation.id,
        from: relation.from_node_id,
        to: relation.to_node_id,
        type: relation.type,
        label: relation.label,
        document_id: relation.document_id,
        confidence: relation.confidence,
        evidence_ids: relation.evidence_ids,
      })),
    };
    return { data, search, graph };
  }

  function fromDocument(sourceDocument = document) {
    const mode = sourceDocument
      .querySelector('meta[name="knowledge-data-source"]')
      ?.getAttribute("content") || "static";
    if (mode === "api") {
      const basePath = sourceDocument
        .querySelector('meta[name="knowledge-api-base"]')
        ?.getAttribute("content") || "./api/v1";
      return new ApiKnowledgeDataSource(basePath);
    }
    return new StaticBundleDataSource(".");
  }

  global.KnowledgeDataSources = Object.freeze({
    ApiKnowledgeDataSource,
    StaticBundleDataSource,
    fromDocument,
  });
})(window);
