import test from "node:test";
import assert from "node:assert/strict";
import { redact, timeline, exportJSON } from "../src/debug.mjs";
test("debug redacts nested credentials while retaining usage counts", () => {
  assert.deepEqual(
    redact({
      model: { api_key: "private", usage: { total_tokens: 12 } },
      error: "Bearer private-value",
      calls: 3,
    }),
    {
      model: { api_key: "[redacted]", usage: { total_tokens: 12 } },
      error: "[redacted]",
      calls: 3,
    },
  );
});
test("timeline reflects overlap rather than inventing sequential stages", () => {
  const bars = timeline([
    { id: "a", started_at: "2026-11-15T00:00:00Z", duration_ms: 1000 },
    { id: "b", started_at: "2026-11-15T00:00:00.500Z", duration_ms: 1000 },
  ]);
  assert.equal(bars[0].left, 0);
  assert.ok(bars[1].left < bars[0].width);
  assert.ok(bars[1].left + bars[1].width <= 100.001);
  assert.deepEqual(timeline([]), []);
});

test("domain export retains long data while masking credentials", () => {
  const payload = {
    rows: Array.from({ length: 250 }, (_, i) => ({
      i,
      value: "x".repeat(17000),
    })),
    api_key: "private",
  };
  const out = JSON.parse(exportJSON(payload));
  assert.equal(out.rows.length, 250);
  assert.equal(out.rows[249].value.length, 17000);
  assert.equal(out.api_key, "[redacted]");
});
