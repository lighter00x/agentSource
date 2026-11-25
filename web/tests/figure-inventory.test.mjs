import test from "node:test";
import assert from "node:assert/strict";
import { figureInventory } from "../src/figure-inventory.mjs";
test("parsed visuals remain visible without extraction and historical/query reports cannot mask absence", () => {
  const root = {
    id: "doc/r2/document",
    type: "document",
    children: [
      { id: "doc/r2/table1", type: "table", children: [] },
      { id: "doc/r2/figure1", type: "figure", children: [] },
    ],
  };
  const nodes = figureInventory(root, [
    { node_ref: "doc/r1/table1", status: "ok" },
    { node_ref: "doc/r2/table1", mode: "query", status: "ok" },
  ]);
  assert.equal(nodes.length, 2);
  assert.ok(nodes.every((n) => n.report === null));
});
test("latest failed extraction is retained even if an older extraction was ok", () => {
  const nodes = figureInventory(
    { id: "doc/r1/table1", type: "table", children: [] },
    [
      { node_ref: "doc/r1/table1", status: "failed" },
      { node_ref: "doc/r1/table1", status: "ok" },
    ],
  );
  assert.equal(nodes[0].report.status, "failed");
});
