import { test, expect } from "@playwright/test";
const now = "2026-11-15T03:00:00Z";
const run = {
  id: "run_failed",
  document_id: "doc_a",
  component: "reader_read",
  run_type: "reader",
  component_version: "0.6.1",
  parent_run_id: null,
  trace_id: "trace_a",
  status: "failed",
  metrics: { model_calls: 2, tool_calls: 0 },
  error: { message: "missing material" },
  started_at: now,
  ended_at: "2026-11-15T03:00:02Z",
  duration_ms: 2000,
};
const doc = {
  document_id: "doc_a",
  title: "示例药物论文",
  filename: "sample.pdf",
  status: "ready",
  active_revision: 2,
  quality: { page_count_pdf: 2 },
  error: null,
};
const node = {
  node_id: "doc_a/r2/p000.b001",
  type: "paragraph",
  label: "摘要",
  page: 0,
  regions: [{ page: 0, bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.4 } }],
  text_raw: "真实节点接口原文",
};
test.beforeEach(async ({ page }) => {
  await page.route("**/healthz", (r) =>
    r.fulfill({ json: { status: "ok", version: "0.5.0" } }),
  );
  await page.route("**/api/v1/**", async (r) => {
    const u = new URL(r.request().url()),
      p = u.pathname;
    if (p.endsWith("/documents"))
      return r.fulfill({
        json: {
          documents: [
            doc,
            { ...doc, document_id: "doc_b", title: "第二篇论文" },
          ],
        },
      });
    if (p.endsWith("/runs"))
      return r.fulfill({
        json: {
          runs: u.searchParams.get("status") === "running" ? [] : [run],
          total: u.searchParams.get("status") === "running" ? 0 : 1,
          limit: 50,
          offset: 0,
          observed_at: now,
          scope: "persisted_history_all_revisions",
        },
      });
    if (p.endsWith("/runs/run_failed"))
      return r.fulfill({
        json: {
          ...run,
          inputs: { from_page: 0 },
          outputs: { status: "failed" },
          model: { usage: { total_tokens: 120 } },
        },
      });
    if (p.endsWith("/tree"))
      return r.fulfill({
        json: {
          root: {
            id: "doc_a/r2/document",
            type: "document",
            page: null,
            label: "文档",
            children: [],
          },
        },
      });
    if (p.endsWith("/nodes")) return r.fulfill({ json: { nodes: [node] } });
    if (p.includes("/nodes/")) return r.fulfill({ json: node });
    if (p.endsWith("/image"))
      return r.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/></svg>',
      });
    if (p.endsWith("/reader-notes"))
      return r.fulfill({
        json: {
          notes: [
            { id: "note1", page: 0, version: 1, payload: { pages_read: [0] } },
          ],
          total: 1,
          notice: "当前 revision 的 active 检查点",
        },
      });
    if (p.endsWith("/reader"))
      return r.fulfill({ json: { status: "pending", last_page: 0 } });
    return r.fulfill({
      status: 404,
      json: { error: { code: "NOT_FOUND", message: "尚无结果" } },
    });
  });
});
test("run details, filter, pause and network debugging", async ({ page }) => {
  await page.goto("/?doc=doc_a");
  await page.getByRole("button", { name: /reader_read failed/ }).click();
  await expect(page.getByText("missing material").first()).toBeVisible();
  await expect(
    page
      .locator("details")
      .filter({ has: page.locator("summary", { hasText: "metrics" }) }),
  ).toContainText("model_calls");
  await page.getByRole("button", { name: "暂停轮询" }).click();
  await expect(page.getByText("已暂停", { exact: true })).toBeVisible();
  await page
    .getByRole("combobox", { name: "状态", exact: true })
    .selectOption("running");
  await expect(page.getByText(/暂无匹配运行记录/)).toBeVisible();
  await page.getByRole("button", { name: /请求调试台/ }).click();
  await expect(
    page.getByText(
      "/api/v1/documents/doc_a/runs?limit=50&offset=0&status=running",
      { exact: true },
    ),
  ).toBeVisible();
});
test("document overlay and reader request use backend page numbering", async ({
  page,
}) => {
  await page.goto("/?doc=doc_a");
  await page.getByRole("button", { name: /文档与证据/ }).click();
  await page.getByRole("button", { name: "摘要", exact: true }).click();
  await expect(
    page.locator(".ocr-preview").getByText("真实节点接口原文", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /调试操作/ }).click();
  await expect(
    page.getByRole("button", { name: "预览并确认请求" }),
  ).toBeDisabled();
  await page.getByLabel("启用本会话写操作").check();
  await page.getByLabel("起始页（留空恢复）").fill("2");
  await page.getByRole("button", { name: "预览并确认请求" }).click();
  await expect(page.getByRole("dialog")).toContainText('"from_page": 1');
  const req = page.waitForRequest(
    (r) => r.url().endsWith("/reader") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "确认执行", exact: true }).click();
  expect((await req).postDataJSON()).toEqual({ from_page: 1 });
  await expect(page.getByText(/"status": "pending"/)).toBeVisible();
});
test("switching document never keeps previous run detail", async ({ page }) => {
  await page.goto("/?doc=doc_a");
  await page.getByRole("button", { name: /reader_read failed/ }).click();
  await expect(page.getByText("选择左侧记录")).toHaveCount(0);
  await page.getByLabel("当前论文", { exact: true }).selectOption("doc_b");
  await expect(page.getByText(/选择左侧记录/)).toBeVisible();
  await expect(page).toHaveURL(/doc=doc_b/);
});
test("disconnected monitor retains and labels previous snapshot", async ({
  page,
}) => {
  await page.goto("/?doc=doc_a");
  await expect(
    page.getByRole("button", { name: /reader_read failed/ }),
  ).toBeVisible();
  await page.route("**/documents/doc_a/runs?**", (r) =>
    r.fulfill({ status: 503, json: { error: "offline" } }),
  );
  // Automatic poll retains previous snapshot; explicit refresh starts a fresh request.
  await expect(
    page.getByText("以下保留上次成功快照，当前连接异常。"),
  ).toBeVisible({ timeout: 10000 });
  await expect(
    page.getByRole("button", { name: /reader_read failed/ }),
  ).toBeVisible();
});

// Close before route teardown so polling never falls through to the real proxy.
test.afterEach(async ({ page }) => {
  await page.close();
});
