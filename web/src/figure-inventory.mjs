// Report presence is separate from independent verification of its contents.
export function figureInventory(root, reports) {
  const found = new Map();
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (
      ["table", "figure", "scheme"].includes(node.type) &&
      typeof node.id === "string"
    )
      found.set(node.id, node);
    for (const child of node.children || []) visit(child);
  };
  visit(root);
  // The API returns newest first. Query-only reports do not replace extraction.
  const latest = new Map();
  for (const report of reports) {
    if (report.mode === "query" || latest.has(report.node_ref)) continue;
    latest.set(report.node_ref, report);
  }
  return [...found.values()].map((node) => ({
    ...node,
    report: latest.get(node.id) || null,
  }));
}
