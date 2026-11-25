import test from "node:test";
import assert from "node:assert/strict";
import {
  traceSteps,
  callImages,
  callStatus,
  callText,
  callSummary,
} from "../src/agent-trace.mjs";
const wrap = (data) => ({
  kind: "agent_exploration",
  stage: "structure",
  at: "2026-11-23",
  data,
});

test("source assignment review stays within its submission and retains source images", () => {
  const steps = traceSteps(
    [
      {
        kind: "tool_start",
        call_id: "submit",
        name: "record_reference_assignments",
      },
      {
        kind: "reference_review",
        parent_call_id: "submit",
        status: "needs_revision",
        image_refs: [{ blob_ref: "source", page: 8, evidence_id: "proof" }],
        issues: [
          {
            assignment_index: 2,
            field: "substituents",
            description: "Source row differs",
          },
        ],
      },
      {
        kind: "tool",
        call_id: "submit",
        name: "record_reference_assignments",
        result: { ok: false },
      },
    ].map(wrap),
  );
  assert.equal(steps.length, 1);
  assert.equal(steps[0].children[0].entry.kind, "reference_review");
  assert.equal(steps[0].children[0].image_refs[0].page, 8);
  assert.equal(steps[0].children[0].entry.issues[0].assignment_index, 2);
});

test("operations retain exact order and keep independent audits inside their invoking operation", () => {
  const events = [
    { kind: "structure_review", status: "needs_revision" },
    { kind: "context", active_images: 1 },
    { kind: "decision", turn: 1 },
    { kind: "tool_start", turn: 1, call_id: "p", name: "patch_structure" },
    { kind: "structure_review", parent_call_id: "p", status: "passed" },
    {
      kind: "tool",
      turn: 1,
      call_id: "p",
      name: "patch_structure",
      result: { ok: true },
    },
  ].map(wrap);
  const steps = traceSteps(events);
  assert.deepEqual(
    steps.map((s) => s.type),
    ["review", "agent", "tool"],
  );
  assert.equal(steps[2].children[0].entry.status, "passed");
  assert.equal(callStatus(steps[2].call), "completed");
});
test("call identifiers handle reordered results without regrouping by conversation turn", () => {
  const steps = traceSteps(
    [
      { kind: "tool_start", turn: 1, call_id: "a", name: "get_node" },
      { kind: "tool_start", turn: 1, call_id: "b", name: "get_node" },
      {
        kind: "tool",
        turn: 1,
        call_id: "b",
        name: "get_node",
        result: { ok: true },
      },
    ].map(wrap),
  );
  assert.equal(callStatus(steps[0].call), "running");
  assert.equal(callStatus(steps[1].call), "completed");
});
test("historical model requests recover the actual input images without using later evidence", () => {
  const steps = traceSteps([
    wrap({ kind: "context", active_images: 1 }),
    {
      kind: "model_request",
      data: {
        task: "figure_explore",
        request: {
          messages: [
            {
              role: "user",
              content: [{ type: "image", blob_ref: "exact-old-image" }],
            },
          ],
        },
      },
    },
    wrap({ kind: "decision", turn: 1 }),
  ]);
  assert.equal(steps[0].entry.image_refs[0].blob_ref, "exact-old-image");
  const missing = traceSteps([
    wrap({ kind: "context", active_images: 2 }),
    wrap({ kind: "decision", turn: 1 }),
  ]);
  assert.equal(missing[0].entry.image_refs, undefined);
});
test("image page numbers and preview precision survive; repeated blob refs deduplicate within a call", () => {
  const result = {
    ok: true,
    image_refs: [
      { blob_ref: "image-1", page: 2 },
      { blob_ref: "image-1", page: 2 },
      { blob_ref: "image-2", display_page: 8 },
    ],
  };
  const images = callImages({ name: "preview_pages", result: { result } });
  assert.deepEqual(
    images.map((i) => i.page),
    [3, 8],
  );
  assert.equal(images[0].precision, "preview");
  assert.equal(images[0].viewed, true);
});
test("text-only page reads attach a source preview without claiming a multimodal read", () => {
  const call = {
    name: "get_page_text",
    result: {
      arguments: { page: 0 },
      result: { ok: true, data: { page: 0, nodes: [{ text: "Formula α" }] } },
    },
  };
  const image = callImages(call, [], "/api/v1/documents/doc_a")[0];
  assert.equal(image.page, 1);
  assert.equal(image.viewed, false);
  assert.equal(image.src, "/api/v1/documents/doc_a/pages/0/image?dpi=100");
  assert.deepEqual(callText(call), ["Formula α"]);
  assert.equal(
    callImages(
      { ...call, result: { ...call.result, result: { ok: false } } },
      [],
      "/api",
    )[0],
    undefined,
  );
});
test("search matches and artifact blob refs do not fabricate viewed images", () => {
  const call = {
    name: "search_text",
    result: {
      result: {
        ok: true,
        data: {
          hits: [{ snippet: "hit", page: 4 }],
          blob_ref: "json-artifact",
        },
      },
    },
  };
  assert.deepEqual(callImages(call, [], "/api"), []);
  assert.deepEqual(callText(call), ["hit"]);
});
test("independent review issues are visible even when the tool itself returned ok", () => {
  assert.equal(
    callStatus({
      result: {
        result: { ok: true, structure_review: { status: "needs_revision" } },
      },
    }),
    "needs_review",
  );
});
test("node source previews use the actual full node reference and preserve route separators", () => {
  const call = {
    name: "get_node",
    result: {
      result: { ok: true, data: { id: "doc/r1/p004.b000", text: "read text" } },
    },
  };
  const image = callImages(call, [], "/api")[0];
  assert.equal(image.src, "/api/v1/nodes/doc/r1/p004.b000/crop?dpi=144&pad=0");
  assert.equal(image.viewed, false);
});
test("fallback supports saved logs when no live event stream is available", () => {
  assert.equal(
    traceSteps([], [{ kind: "decision", turn: 7 }])[0].decision.turn,
    7,
  );
});

test("section text reads attach each referenced source page once", () => {
  const call = {
    name: "get_section",
    result: {
      result: {
        ok: true,
        data: {
          nodes: [
            { page: 2, text: "a" },
            { page: 2, text: "b" },
            { page: 3, text: "c" },
          ],
        },
      },
    },
  };
  const images = callImages(call, [], "/api/v1/documents/doc_a");
  assert.deepEqual(
    images.map((i) => i.page),
    [3, 4],
  );
  assert.ok(images.every((i) => !i.viewed));
});

test("known workflow rejections remain distinct from service and chemistry failures", () => {
  const rejected = {
    result: {
      result: {
        ok: false,
        error:
          "ValueError: Initial extraction already exists. Use patch_structure",
      },
    },
  };
  assert.equal(callStatus(rejected), "blocked");
  assert.equal(
    callStatus({
      result: { result: { ok: false, error: "TimeoutError: model timed out" } },
    }),
    "failed",
  );
  assert.equal(
    callStatus({
      result: { result: { ok: false, error: "ValueError: invalid_scaffold" } },
    }),
    "failed",
  );
});

test("successive model inputs expose only newly added images while retaining exact complete lists", () => {
  const refs = (...ids) => ids.map((blob_ref) => ({ blob_ref }));
  const steps = traceSteps(
    [
      { kind: "context", image_refs: refs("target") },
      { kind: "decision" },
      { kind: "context", image_refs: refs("target", "page") },
      { kind: "decision" },
      { kind: "context", image_refs: refs("target", "page") },
    ].map(wrap),
  );
  assert.equal(steps[0].inputDelta.known, false);
  assert.deepEqual(steps[1].inputDelta.added, refs("page"));
  assert.deepEqual(steps[2].inputDelta.added, []);
  assert.deepEqual(steps[2].entry.image_refs, refs("target", "page"));
});

test("preview summaries explicitly distinguish validation from committed edits", () => {
  const preview = {
    name: "preview_structure_patch",
    result: { result: { ok: true, dry_run: true, patch: { changes: [] } } },
  };
  assert.match(callSummary(preview), /尚未应用/);
  assert.equal(callStatus(preview), "completed");
  const read = {
    name: "get_structure",
    result: {
      result: {
        ok: true,
        extraction_version: 3,
        path: "/pathways",
        has_more: true,
      },
    },
  };
  assert.match(callSummary(read), /版本 3/);
  assert.match(callSummary(read), /分页/);
});

test("repair feedback identifies arbitrary missing attachment keys and control rejections", () => {
  const call = {
    name: "record_reference_assignments",
    result: {
      result: {
        ok: false,
        repair: { code: "missing_attachment_values", missing_keys: ["Site-K"] },
        error: "raw validation error",
      },
    },
  };
  assert.match(callSummary(call), /Site-K/);
  assert.equal(callStatus(call), "failed");
  const phase = {
    result: {
      result: { ok: false, repair: { code: "phase_constraint" }, error: "raw" },
    },
  };
  assert.equal(callStatus(phase), "blocked");
});
