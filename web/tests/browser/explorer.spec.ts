import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
async function shot(page: import("@playwright/test").Page, name: string) {
  if (process.env.WORKBENCH_SCREENSHOT_DIR) {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: resolve(process.env.WORKBENCH_SCREENSHOT_DIR, name),
      fullPage: name.includes("chart"),
    });
  }
}
const ref = "doc_a/r2/p002.g001";
const evidence = [{ node_id: ref, page: 2, char_span: [1, 5] }];
const compound = {
  compound_id: "cmp26",
  primary_label: "26",
  aliases: ["UCM-1306"],
  role: "final",
  role_status: "needs_review",
  accepted_structure_id: "str26",
  evidence,
};
const reports = [
  {
    report_id: "report_table",
    node_ref: ref,
    image_type: "table",
    status: "needs_review",
    run_id: "run_figure",
    extraction: {
      title: "Table 3",
      header_rows: [[{ text: "Compound" }, { text: "IC50" }]],
      rows: [
        [
          { text: "26" },
          {
            text: "12",
            status: "unclear",
            bbox: { x0: 0.2, y0: 0.3, x1: 0.4, y1: 0.5 },
          },
        ],
      ],
      footnote_markers: { a: "test condition" },
    },
    checks: [{ code: "V-T01", severity: "warning", message: "受控测试待复核" }],
    trace: [
      { step: "table_extract", run_id: "run_skill", status: "completed" },
    ],
  },
  {
    report_id: "report_chart",
    node_ref: ref,
    image_type: "chart",
    status: "ok",
    extraction: {
      chart_type: "line",
      x_axis: { title: "浓度", scale: "log10", unit: "nM" },
      y_axis: { title: "抑制率", scale: "linear", unit: "%" },
      series: [
        {
          label: "受控曲线",
          points: [
            [1, 10],
            [10, 40],
            [100, 80],
          ],
        },
      ],
    },
  },
  {
    report_id: "report_old",
    node_ref: "doc_a/r1/p002.g001",
    image_type: "other",
    status: "failed",
    extraction: {},
    checks: [],
  },
];
test.beforeEach(async ({ page }) => {
  await page.route("**/healthz", (r) =>
    r.fulfill({ json: { status: "ok", version: "test" } }),
  );
  await page.route("**/api/v1/**", async (r) => {
    const u = new URL(r.request().url()),
      p = u.pathname;
    if (p.endsWith("/documents"))
      return r.fulfill({
        json: {
          documents: [
            {
              document_id: "doc_a",
              title: "受控界面联调论文",
              active_revision: 2,
              status: "ready",
              quality: { page_count_pdf: 3 },
            },
          ],
        },
      });
    if (p.endsWith("/compounds/cmp26"))
      return r.fulfill({
        json: {
          compound,
          structures: [
            {
              structure_id: "str26",
              source: "manual",
              smiles_core: "CCO",
              canonical_smiles: "CCO",
              e_smiles_raw: "CCO",
              evidence,
            },
          ],
          conflicts: [{ type: "test_conflict", message: "保留测试冲突" }],
        },
      });
    if (p.endsWith("/compounds")) return r.fulfill({ json: [compound] });
    if (p.endsWith("/measurements"))
      return r.fulfill({
        json: [
          {
            measurement_id: "m1",
            subject_label: "26",
            metric: "IC50",
            value_raw: "12",
            unit_raw: "nM",
            status: "needs_review",
            source_kind: "table",
            assay_id: "assay1",
            evidence,
          },
        ],
      });
    if (p.endsWith("/assays"))
      return r.fulfill({
        json: [
          {
            assay_id: "assay1",
            name_raw: "测试测定",
            species: "human",
            target: "D1",
            conditions: { temperature: "37C" },
            evidence,
          },
        ],
      });
    if (p.endsWith("/figure-reports")) return r.fulfill({ json: reports });
    if (p.endsWith("/full-text"))
      return r.fulfill({
        json: {
          status: "needs_review",
          run_id: "run_full",
          projection: {
            delivery: {
              总结: "受控全文结果",
              steps: { "1": { change: "甲基化" } },
            },
            audit: {
              总结: {
                value: "受控全文结果",
                evidence,
                source_ids: ["artifact1"],
              },
              steps: {
                "1": { change: { value: "甲基化", evidence, source_ids: [] } },
              },
            },
            warnings: ["受控待复核"],
          },
        },
      });
    if (p.endsWith("/reader-notes"))
      return r.fulfill({
        json: {
          notes: [
            {
              id: "note1",
              page: 2,
              version: 1,
              fingerprint: "test_fp",
              payload: {
                notes: {
                  pages_read: [0, 1, 2],
                  model_calls: 2,
                  tool_calls: 0,
                  open_questions: [{ question: "测试疑问" }],
                  lookup_answers: [
                    {
                      question: "测试回查",
                      answer: "受控答案",
                      status: "found",
                      evidence,
                    },
                  ],
                },
                deltas: [
                  {
                    thesis_updates: [
                      { text: "本页主题增量", evidence_nodes: [ref] },
                    ],
                  },
                ],
              },
            },
          ],
          total: 1,
          notice: "受控检查点",
        },
      });
    if (p.endsWith("/search"))
      return r.fulfill({
        json: {
          results: [
            {
              node_id: ref,
              page: 2,
              type: "table",
              snippet: "受控命中片段",
              char_span: [1, 5],
            },
          ],
          truncated: false,
        },
      });
    if (p.endsWith("/tree"))
      return r.fulfill({
        json: {
          root: {
            id: "doc_a/r2/document",
            type: "document",
            page: null,
            children: [],
          },
        },
      });
    if (p.endsWith("/nodes"))
      return r.fulfill({
        json: {
          nodes: [
            {
              node_id: ref,
              type: "table",
              label: "来源表格",
              page: 2,
              regions: [
                { page: 2, bbox: { x0: 0.1, y0: 0.1, x1: 0.9, y1: 0.9 } },
              ],
            },
          ],
        },
      });
    if (p.includes("/nodes/") && !p.endsWith("/crop"))
      return r.fulfill({
        json: {
          node_id: ref,
          page: 2,
          text_raw: "🧪证据原文用于定位测试",
          regions: [],
        },
      });
    if (p.endsWith("/image") || p.endsWith("/crop"))
      return r.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/></svg>',
      });
    if (p.endsWith("/runs")) return r.fulfill({ json: { runs: [], total: 0 } });
    if (p.includes("/runs/"))
      return r.fulfill({
        json: {
          id: p.split("/").pop(),
          metrics: { model_calls: 3 },
          inputs: {},
          outputs: { status: "completed" },
        },
      });
    if (p.endsWith("/skills/catalog"))
      return r.fulfill({
        json: [{ name: "table_extract", l0: "表格抽取", l1: "受控能力描述" }],
      });
    if (p.endsWith("/versions"))
      return r.fulfill({
        json: [{ version: "0.5.0", status: "active", hash: "test_hash" }],
      });
    if (p.endsWith("/batches/batch_test"))
      return r.fulfill({
        json: {
          status: "running",
          document_ids: ["doc_a"],
          task_ids: ["task1"],
          results: {},
        },
      });
    return r.fulfill({ status: 404, json: { error: { code: "NOT_FOUND" } } });
  });
});
test("compound structure renders locally and measurements link to exact evidence", async ({
  page,
}) => {
  await page.goto("/?doc=doc_a&view=compounds");
  await page.getByRole("button", { name: /26 UCM-1306/ }).click();
  await expect(page.getByAltText("化合物结构图")).toBeVisible({
    timeout: 15000,
  });
  expect(
    await page
      .getByAltText("化合物结构图")
      .evaluate((img: HTMLImageElement) => img.naturalWidth),
  ).toBeGreaterThan(0);
  await expect(page.getByText("1 项待处理冲突")).toBeVisible();
  await shot(page, "controlled-compound.png");
  await page.getByRole("button", { name: /测定数据/ }).click();
  await page.getByRole("button", { name: "查看条件" }).click();
  await expect(page.getByText("human", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "证据 1", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("页码", { exact: true })).toHaveValue("3");
  await expect(page.locator("mark")).toHaveText("证据原文");
  await expect(page.locator(".region.active")).toHaveCount(1);
});
test("figure table/chart rendering, historical evidence and step-run navigation", async ({
  page,
}) => {
  await page.goto("/?doc=doc_a&view=figures");
  await page.getByRole("button", { name: /table needs_review/ }).click();
  await expect(
    page.getByRole("heading", { name: "Table 3", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /12 unclear/ }).click();
  await expect(
    page.getByRole("heading", { name: "单元格 · 行 1 / 列 2" }),
  ).toBeVisible();
  await expect(page.getByLabel("原图单元格高亮")).toBeVisible();
  await page.getByRole("button", { name: /chart ok/ }).click();
  await expect(
    page.getByRole("img", { name: "已保存图表数据预览" }),
  ).toBeVisible();
  await shot(page, "controlled-chart.png");
  await page.getByRole("button", { name: /other failed/ }).click();
  await page.getByRole("button", { name: "证据 1", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("历史 revision 1");
  await page.getByRole("button", { name: /table needs_review/ }).click();
  await page.getByText("Skill 执行过程与调用统计", { exact: true }).click();
  await page.getByRole("button", { name: "查看步骤 Run" }).click();
  await expect(page.getByText("run_skill", { exact: true })).toBeVisible();
  await expect(
    page
      .locator("details")
      .filter({ has: page.locator("summary", { hasText: "metrics" }) }),
  ).toContainText("3");
});
test("structured result, page notes, search, capabilities and batch query", async ({
  page,
}) => {
  await page.goto("/?doc=doc_a&view=result");
  await expect(page.getByText("受控全文结果", { exact: true })).toBeVisible();
  await expect(page.getByText("受控待复核", { exact: true })).toBeVisible();
  await shot(page, "controlled-result.png");
  await page.getByRole("button", { name: /阅读检查点/ }).click();
  await page.getByRole("button", { name: /第 3 页/ }).click();
  await expect(page.getByText("本页主题增量", { exact: true })).toBeVisible();
  await expect(page.getByText("受控答案", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /论文检索/ }).click();
  await page.getByLabel("检索内容").fill("UCM-1306");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(page.getByText("受控命中片段")).toBeVisible();
  await page.getByRole("button", { name: /能力与版本/ }).click();
  await page.getByRole("button", { name: "检查注册版本" }).click();
  await expect(page.getByText("test_hash")).toBeVisible();
  await page.getByRole("button", { name: /批次状态/ }).click();
  await page.getByLabel("批次 ID").fill("batch_test");
  await page.getByRole("button", { name: "查看批次" }).click();
  await expect(page.getByText("任务数 1 · 文档数 1")).toBeVisible();
});
test("missing full-text result is an explicit empty state", async ({
  page,
}) => {
  await page.route("**/full-text", (r) =>
    r.fulfill({ status: 404, json: { error: { code: "NOT_FOUND" } } }),
  );
  await page.goto("/?doc=doc_a&view=result");
  await expect(page.getByText(/当前论文尚无有效全文结果/)).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// Close before route teardown so polling never falls through to the real proxy.
test.afterEach(async ({ page }) => {
  await page.close();
});

test("invalid proxy response shows an actionable JSON contract error", async ({
  page,
}) => {
  await page.route("**/compounds?**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html>wrong proxy</html>",
    }),
  );
  await page.goto("/?doc=doc_a&view=compounds");
  await expect(page.getByRole("alert")).toContainText("接口未返回 JSON");
});

test("parsed table is visible with a source preview even when extraction has never produced reports", async ({
  page,
}) => {
  await page.route("**/tree", (r) =>
    r.fulfill({
      json: {
        root: {
          id: "doc_a/r2/document",
          type: "document",
          page: null,
          children: [
            { id: ref, type: "table", label: "Table 3", page: 2, children: [] },
          ],
        },
      },
    }),
  );
  await page.route("**/figure-reports", (r) => r.fulfill({ json: [] }));
  await page.goto("/?doc=doc_a&view=figures");
  await expect(page.getByText(/其中 1 个尚无提取报告/)).toBeVisible();
  await page.getByRole("button", { name: /Table 3 pending/ }).click();
  await expect(
    page.getByRole("heading", { name: "源图 · 尚无提取报告" }),
  ).toBeVisible();
  await expect(page.getByAltText("原图")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});
