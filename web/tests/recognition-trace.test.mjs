import test from "node:test";
import assert from "node:assert/strict";
import { recognitionTrace } from "../src/recognition-trace.mjs";
const event = (kind, data) => ({ kind, data });
const detected = [
  event("skill_result", {
    name: "molecule_detect",
    payload: {
      boxes: [
        {
          reading_index: 2,
          local_id: "fig.m007",
          local_bbox: { x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.6 },
          parent_region_index: 0,
        },
        {
          reading_index: 9,
          local_id: "fig.m008",
          local_bbox: { x0: 0.6, y0: 0.2, x1: 0.8, y1: 0.6 },
          parent_region_index: 0,
        },
      ],
    },
  }),
];
test("pairs detector ID, actual crop and structure instead of the misleading legacy whole-image log", () => {
  const rows = recognitionTrace(
    [
      event("skill_start", {
        name: "molecule_recognize",
        target: "doc/r1/fig.m007",
        images: [{ blob_ref: "whole-image" }],
      }),
      event("tool_request", {
        name: "molparser.recognize",
        arguments: { image_refs: ["crop-2"] },
      }),
      event("tool_response", {
        name: "molparser.recognize",
        result: { results: [{ input_index: 0, esmi: "CCO" }] },
      }),
      event("skill_result", {
        name: "molecule_recognize",
        payload: {
          structures: [
            { node_ref: "doc/r1/fig.m007", canonical_smiles: "CCO" },
          ],
        },
      }),
      event("manual_reuse", {
        node_ref: "doc/r1/fig.m008",
        mol_id: 9,
        structure: { canonical_smiles: "CCN" },
      }),
    ],
    detected,
  );
  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map((r) => r.mol_id),
    [2, 9],
  );
  assert.equal(rows[0].crop_ref, "crop-2");
  assert.equal(rows[0].raw_result.esmi, "CCO");
  assert.equal(rows[1].source, "manual");
  assert.equal(rows[1].crop_ref, undefined);
});
test("batch input indices are local to each tool call and bind to each returned node", () => {
  const rows = recognitionTrace(
    [
      event("skill_start", {
        name: "molecule_recognize",
        target: "doc/r1/fig",
      }),
      event("tool_request", {
        name: "molparser.recognize",
        arguments: { image_refs: ["crop-2"] },
      }),
      event("tool_response", {
        name: "molparser.recognize",
        result: { results: [{ input_index: 0, esmi: "CCO" }] },
      }),
      event("tool_request", {
        name: "molparser.recognize",
        arguments: { image_refs: ["crop-9"] },
      }),
      event("tool_response", {
        name: "molparser.recognize",
        result: { results: [{ input_index: 0, error: "cannot recognize" }] },
      }),
      event("skill_result", {
        name: "molecule_recognize",
        payload: {
          structures: [
            { node_ref: "doc/r1/fig.m007", canonical_smiles: "CCO" },
            {
              node_ref: "doc/r1/fig.m008",
              validation: { valid: false, error: "cannot recognize" },
            },
          ],
        },
      }),
    ],
    detected,
  );
  assert.deepEqual(
    rows.map((r) => r.crop_ref),
    ["crop-2", "crop-9"],
  );
  assert.equal(rows[1].raw_result.error, "cannot recognize");
});
test("historical missing crops and missing calls remain explicit; manual data comes only from same-run snapshots", () => {
  const rows = recognitionTrace(
    [
      event("skill_start", {
        name: "molecule_recognize",
        target: "doc/r1/fig.m007",
        images: [{ blob_ref: "whole" }],
      }),
      event("skill_result", {
        name: "molecule_recognize",
        payload: {
          structures: [
            { node_ref: "doc/r1/fig.m007", canonical_smiles: "CCO" },
          ],
        },
      }),
    ],
    detected,
    [
      {
        node_ref: "doc/r1/fig.m008",
        source: "manual",
        canonical_smiles: "CCN",
      },
    ],
  );
  assert.equal(rows[0].crop_ref, undefined);
  assert.equal(rows[1].source, "manual");
  assert.equal(rows[1].events.length, 0);
});

test("cached recognition never claims a crop was sent in this run", () => {
  const rows = recognitionTrace(
    [
      event("skill_start", {
        name: "molecule_recognize",
        target: "doc/r1/fig.m007",
        images: [{ blob_ref: "crop", role: "molecule_crop" }],
      }),
      event("skill_result", {
        name: "molecule_recognize",
        trace: { cached: true },
        payload: {
          structures: [
            { node_ref: "doc/r1/fig.m007", canonical_smiles: "CCO" },
          ],
        },
      }),
    ],
    detected,
  );
  assert.equal(rows[0].source, "cache");
  assert.equal(rows[0].crop_ref, undefined);
});
test("explicit re-recognition of a manually corrected molecule remains a single card with its override note", () => {
  const rows = recognitionTrace(
    [
      event("skill_start", {
        name: "molecule_recognize",
        target: "doc/r1/fig.m007",
      }),
      event("skill_result", {
        name: "molecule_recognize",
        payload: {
          structures: [
            { node_ref: "doc/r1/fig.m007", canonical_smiles: "CCO" },
          ],
        },
      }),
      event("manual_reuse", {
        node_ref: "doc/r1/fig.m007",
        reason: "人工结构仍为最终结果",
      }),
    ],
    detected,
  );
  assert.equal(rows.filter((r) => r.mol_id === 2).length, 1);
  assert.equal(rows[0].manual_override, true);
  assert.equal(rows[0].structure.canonical_smiles, "CCO");
});

test("bbox previews bind to the original image of the detected segment without inventing tool input", () => {
  const detection = structuredClone(detected);
  detection[0].data.payload.image_refs = ["source-0", "source-1"];
  detection[0].data.payload.boxes[1].parent_region_index = 1;
  const cards = recognitionTrace([], detection);
  assert.deepEqual(
    cards.map((c) => c.source_image_ref),
    ["source-0", "source-1"],
  );
  assert.deepEqual(
    cards.map((c) => c.crop_ref),
    [undefined, undefined],
  );
});
