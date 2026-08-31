"use strict";

(function exposeRelatedDocuments(global) {
  const DEFAULT_LIMIT = 5;

  function normalizedText(value) {
    return typeof value === "string" ? value.trim().normalize("NFKC").toLowerCase() : "";
  }

  function uniqueTags(value) {
    const labels = new Map();
    for (const item of Array.isArray(value) ? value : []) {
      if (typeof item !== "string") continue;
      const label = item.trim();
      const key = normalizedText(label);
      if (key && !labels.has(key)) labels.set(key, label);
    }
    return labels;
  }

  function commonPrefixDepth(left, right) {
    const a = Array.isArray(left) ? left : [];
    const b = Array.isArray(right) ? right : [];
    let depth = 0;
    while (depth < a.length && depth < b.length && a[depth] === b[depth]) depth += 1;
    return depth;
  }

  function sourceIdentity(documentItem) {
    const sourceId = normalizedText(documentItem?.source_id);
    if (sourceId) return sourceId;
    return normalizedText(documentItem?.source?.sha256);
  }

  function compareText(left, right) {
    const a = String(left || "");
    const b = String(right || "");
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  }

  function nodeLookup(nodes, nodeId) {
    if (!nodeId) return null;
    if (nodes && typeof nodes.get === "function") return nodes.get(nodeId) || null;
    if (!Array.isArray(nodes)) return null;
    return nodes.find((node) => node?.id === nodeId) || null;
  }

  function scoreRelatedDocument(current, candidate, nodes) {
    if (!current || !candidate || !candidate.id || current.id === candidate.id) return null;

    let score = 0;
    const signals = [];
    const currentSource = sourceIdentity(current);
    const candidateSource = sourceIdentity(candidate);
    if (currentSource && currentSource === candidateSource) {
      score += 120;
      signals.push({ type: "same-source", label: "同一原文" });
    }

    const currentTags = uniqueTags(current.tags);
    const candidateTags = uniqueTags(candidate.tags);
    const sharedTags = [...currentTags.keys()].filter((tag) => candidateTags.has(tag));
    if (sharedTags.length) {
      score += Math.min(3, sharedTags.length) * 18;
      signals.push({
        type: "shared-tags",
        label: `共同标签 · ${sharedTags.slice(0, 2).map((tag) => currentTags.get(tag)).join("、")}`,
        tags: sharedTags,
      });
    }

    const sharedPathDepth = commonPrefixDepth(current.path, candidate.path);
    if (current.node_id && current.node_id === candidate.node_id) {
      score += 40;
      signals.push({ type: "same-node", label: "同一专业" });
    } else {
      const currentNode = nodeLookup(nodes, current.node_id);
      const candidateNode = nodeLookup(nodes, candidate.node_id);
      if (
        currentNode?.parent_id
        && currentNode.parent_id === candidateNode?.parent_id
        && sharedPathDepth >= 2
      ) {
        score += 18;
        signals.push({ type: "same-parent", label: "相邻专业方向" });
      }
    }

    if (sharedPathDepth > 1) score += Math.min(4, sharedPathDepth - 1) * 6;
    if (!signals.length || score < 12) return null;

    return {
      document: candidate,
      reason: signals[0].label,
      score,
      signals,
    };
  }

  function rankRelatedDocuments(current, documents, nodes, limit = DEFAULT_LIMIT) {
    const maximum = Number.isSafeInteger(limit) ? Math.max(0, Math.min(20, limit)) : DEFAULT_LIMIT;
    if (!current || maximum === 0) return [];
    return (Array.isArray(documents) ? documents : [])
      .map((candidate) => scoreRelatedDocument(current, candidate, nodes))
      .filter(Boolean)
      .sort((left, right) => {
        const byScore = right.score - left.score;
        if (byScore) return byScore;
        const byDate = String(right.document.updated_at || "")
          .localeCompare(String(left.document.updated_at || ""));
        if (byDate) return byDate;
        const byTitle = compareText(left.document.title, right.document.title);
        return byTitle || compareText(left.document.id, right.document.id);
      })
      .slice(0, maximum);
  }

  global.KnowledgeRelatedDocuments = Object.freeze({
    rankRelatedDocuments,
    scoreRelatedDocument,
  });
})(typeof window !== "undefined" ? window : globalThis);
