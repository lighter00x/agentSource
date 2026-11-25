import test from "node:test";
import assert from "node:assert/strict";
import { invocationTrace } from "../src/invocation-trace.mjs";
const e = (kind, data = {}) => ({ kind, data });
test("multiple rounds retain call order, failures and transport retries", () => {
  const rows = invocationTrace([
    e("skill_start", { name: "extract" }),
    e("model_request", { task: "extract" }),
    e("transport_request", { attempt: 1 }),
    e("transport_error", { error: "timeout" }),
    e("transport_retry", { next_attempt: 2 }),
    e("model_response", { tool_calls: [{ name: "search" }] }),
    e("tool_request", { name: "search", arguments: { query: "A" } }),
    e("tool_response", { name: "search", result: { text: "first" } }),
    e("model_request", { task: "extract" }),
    e("model_response", { content: "done" }),
    e("skill_result", { name: "extract", payload: { done: true } }),
  ]);
  assert.deepEqual(
    rows.map((r) => [r.type, r.round]),
    [
      ["skill", 0],
      ["model", 1],
      ["tool", 1],
      ["model", 2],
    ],
  );
  assert.equal(rows[0].result.data.payload.done, true);
  assert.equal(rows[1].transport.length, 3);
  assert.equal(rows[2].result.data.result.text, "first");
});
test("explicit call IDs disambiguate parallel requests of the same tool", () => {
  const rows = invocationTrace([
    e("tool_request", { name: "search", call_id: "a" }),
    e("tool_request", { name: "search", call_id: "b" }),
    e("tool_response", { name: "search", call_id: "b", result: 2 }),
    e("tool_error", { name: "search", call_id: "a", error: "bad" }),
  ]);
  assert.equal(rows[0].result.data.error, "bad");
  assert.equal(rows[1].result.data.result, 2);
});
test("unfinished and orphaned responses stay visible without invented requests", () => {
  const rows = invocationTrace([
    e("tool_response", { name: "old", result: "saved" }),
    e("tool_request", { name: "waiting" }),
  ]);
  assert.equal(rows[0].start, null);
  assert.equal(rows[1].result, null);
});
