import { test, expect } from "@playwright/test";
const ref = "doc_a/r1/p000.g000",
  mol = ref + ".m000";
const box = { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.7 };
const stages = [
  ["classify", "类型判断"],
  ["plan", "Skill 路由"],
  ["detect", "分子检测"],
  ["recognize", "分子识别"],
  ["som", "编号图"],
  ["structure", "结构化提取"],
  ["post", "回填与映射"],
  ["check_l1", "确定性检查"],
  ["check_l2", "模型复核"],
  ["report", "图表报告"],
  ["deliver", "精简过程结果"],
].map(([id, label]) => ({ id, label }));
async function mock(page: import("@playwright/test").Page, fail = false) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  let version = 0,
    draftVersion = 0;
  const run = {
    id: "wd_old",
    created_at: "2026-11-20T00:00:00Z",
    status: "paused",
    start: "classify",
    next_stage: "som",
    steps: stages
      .slice(0, 4)
      .map((s) => ({ stage: s.id, status: "completed" })),
    correction_version: 0,
    draft_version: 0,
  };
  const node = {
    node_id: ref,
    type: "table",
    label: "Table 1",
    regions: [{ page: 0, bbox: box }],
    text_raw: "Table 1 OCR",
  };
  const state = {
    classification: { image_type: "data_table", confidence: 0.9 },
    chain: [
      "molecule_detect",
      "molecule_recognize",
      "som_render",
      "table_extract",
    ],
  };
  await page.route("**/healthz", (r) =>
    r.fulfill({ json: { status: "ok", version: "test" } }),
  );
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = decodeURIComponent(url.pathname);
    if (req.method() !== "GET")
      posts.push({ path, body: req.postDataJSON() || {} });
    let result: unknown = {};
    if (path === "/api/v1/documents")
      result = {
        documents: [
          {
            document_id: "doc_a",
            filename: "test.pdf",
            status: "ready",
            active_revision: 1,
            quality: { page_count_pdf: 1 },
            meta: {},
          },
        ],
      };
    else if (path.endsWith("/workbench"))
      result = {
        revision: 1,
        mode: "manual",
        stages,
        image_types: ["data_table", "sar_table"],
        figures: [
          {
            node_ref: ref,
            type: "table",
            label: "Table 1",
            regions: node.regions,
            latest: run,
          },
        ],
        counts: { paused: 1 },
      };
    else if (path.endsWith("/layout")) result = { nodes: [node] };
    else if (path.endsWith("/molecules"))
      result = {
        version,
        molecules: [
          {
            node_ref: mol,
            status: "active",
            geometry: {
              regions: [
                { page: 0, bbox: { x0: 0.2, y0: 0.3, x1: 0.4, y1: 0.4 } },
              ],
              local_bbox: { x0: 0.1, y0: 0.1, x1: 0.4, y1: 0.3 },
              parent_region_index: 0,
            },
            attributes: {},
            structure: {
              formula: "C2H6O",
              canonical_smiles: "CCO",
              e_smiles_raw: "CCO",
            },
            manual_smiles: version > 0,
            manual_box: false,
          },
        ],
        history: [],
      };
    else if (path.endsWith("/corrections")) {
      version++;
      result = { version, stale_from: "structure" };
    } else if (path.endsWith("/files")) {
      if (req.method() === "PUT") {
        draftVersion++;
        result = { version: draftVersion, status: "saved_local" };
      } else if (url.searchParams.has("path"))
        result = {
          version: draftVersion,
          content: "Local skill instructions",
          baseline: "Global skill instructions",
          local: draftVersion > 0,
        };
      else
        result = {
          version: draftVersion,
          files: ["drugpaper/skills/builtin/visual_classify/SKILL.md"],
          changed: [],
        };
    } else if (path.endsWith("/file"))
      result = { content: "Actual historical instructions" };
    else if (path.endsWith("/runs")) {
      if (req.method() === "POST") {
        if (fail)
          return route.fulfill({
            status: 503,
            json: { detail: "调试服务暂不可用" },
          });
        result = { id: "wd_new", status: "queued" };
      } else result = [run];
    } else if (path.includes("/workbench/runs/"))
      result = {
        run,
        states: { classify: state, plan: state, recognize: state },
        events: [
          {
            stage: "classify",
            kind: "model_request",
            at: "2026-11-20",
            data: {
              model: "controlled-model",
              request: { text: "classify fixture" },
            },
          },
        ],
      };
    else if (path.includes("/nodes/")) result = node;
    else if (path.endsWith("/image"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="40" y="100">Controlled PDF fixture</text></svg>',
      });
    else if (path.endsWith("/validate")) result = { ok: true };
    return route.fulfill({ json: result });
  });
  return posts;
}

test("stage workbench: automatic routing action, molecule correction, local skill and inherited resume", async ({
  page,
}) => {
  const posts = await mock(page);
  await page.goto("/?doc=doc_a&view=workflow");
  await expect(
    page.getByRole("heading", { name: "全流程工作区", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "类型判断结果", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "解析此图（自动分类与路由）", exact: true })
    .click();
  await expect
    .poll(() =>
      posts.some(
        (p) =>
          p.path.endsWith("/runs") &&
          p.body.start === "classify" &&
          !p.body.skill,
      ),
    )
    .toBe(true);
  await page.getByRole("button", { name: `分子 ${mol}`, exact: true }).click();
  await expect(page.getByText("分子式：C2H6O")).toBeVisible();
  await page.getByLabel("分子 SMILES", { exact: true }).fill("CCN");
  await page
    .getByRole("button", { name: "校验并保存局部 SMILES", exact: true })
    .click();
  await expect
    .poll(() =>
      posts.some((p) => p.body.kind === "smiles" && p.body.smiles === "CCN"),
    )
    .toBe(true);
  await page
    .getByRole("button", { name: "继承上游并继续", exact: true })
    .click();
  await expect
    .poll(() => posts.some((p) => p.body.start === "som" && p.body.source_run))
    .toBe(true);
  await page
    .getByRole("button", { name: "局部 Skill 编辑", exact: true })
    .click();
  await page
    .getByLabel("Skill 内容", { exact: true })
    .fill("Updated local skill");
  await page
    .getByRole("button", { name: "保存当前图表局部版本", exact: true })
    .click();
  await expect
    .poll(() =>
      posts.some(
        (p) =>
          p.path.endsWith("/files") && p.body.content === "Updated local skill",
      ),
    )
    .toBe(true);
  await page.getByText("所选运行实际使用的文件快照", { exact: true }).click();
  await expect(
    page.getByText("Actual historical instructions", { exact: true }),
  ).toBeVisible();
  if (process.env.WORKBENCH_SCREENSHOT_DIR)
    await page.screenshot({
      path: process.env.WORKBENCH_SCREENSHOT_DIR + "/stage-debug.png",
      fullPage: true,
    });
});

test("failed execution stays an explicit error", async ({ page }) => {
  await mock(page, true);
  await page.goto("/?doc=doc_a&view=workflow");
  await page
    .getByRole("button", { name: "解析此图（自动分类与路由）", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "503" }),
  ).toBeVisible();
});

test("text blocks render typed and delimited formulas, with raw source and safe failure", async ({
  page,
}) => {
  const posts = await mock(page);
  const textRef = "doc_a/r1/p000.b001";
  const text = String.raw`Energy E=mc^2. Inline $x_1^2$, display $$\frac{a}{b}$$, brackets \[\sqrt{x}\], parentheses \(y^2\). Broken $\notARealCommand{x}$. <img src=x onerror=alert(1)>`;
  const textNode = {
    node_id: textRef,
    type: "paragraph",
    label: "正文公式测试",
    regions: [{ page: 0, bbox: box }],
    text_raw: text,
    attributes: {
      ocr_lines: [
        {
          spans: [
            { type: "text", content: "Energy " },
            { type: "inline_equation", content: "E=mc^2" },
            { type: "text", content: text.slice("Energy E=mc^2".length) },
          ],
        },
      ],
    },
  };
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/layout"))
      return route.fulfill({ json: { nodes: [textNode] } });
    if (path === `/api/v1/nodes/${textRef}`)
      return route.fulfill({ json: textNode });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=layout");
  await page.getByRole("button", { name: "正文公式测试", exact: true }).click();
  const preview = page.locator(".ocr-preview");
  await expect(preview.locator(".katex")).toHaveCount(5);
  await expect(preview.locator(".katex-display")).toHaveCount(2);
  await expect(preview.locator(".ocr-formula-error")).toContainText(
    "公式渲染失败",
  );
  await expect(preview).toContainText("<img src=x onerror=alert(1)>");
  await expect(preview.locator("img")).toHaveCount(0);
  await page.getByText("原始 OCR / 公式源码", { exact: true }).click();
  await expect(page.locator(".ocr-content pre").first()).toHaveText(text);
  expect(posts).toEqual([]);
});

test("backfilled result uses a read-only JSON code editor and keeps complete downloads", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_done",
    status: "needs_review",
    created_at: "2026-11-21",
    steps: [
      { stage: "post", status: "completed" },
      { stage: "report", status: "completed" },
    ],
    correction_version: 0,
    draft_version: 0,
  };
  const result = {
    schema_version: "figure-result/1.0",
    node_ref: ref,
    run_id: run.id,
    processing_stage: "reported",
    status: "needs_review",
    extraction: {
      items: Array.from({ length: 230 }, (_, i) => ({
        label: `compound_${i}`,
        value: i,
      })),
      description: "A".repeat(17000) + "END",
      api_key: "must-not-appear",
    },
    quality: { checks: [] },
  };
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_done/result"))
      return route.fulfill({
        json: result,
        headers: {
          "Content-Disposition": 'attachment; filename="final-result.json"',
        },
      });
    if (path.endsWith("/workbench/runs/wd_done"))
      return route.fulfill({
        json: {
          run,
          states: {
            post: {
              classification: { image_type: "data_table" },
              chain: ["table_extract"],
            },
          },
          events: [],
        },
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=workflow");
  const panel = page.locator(".result-file");
  await expect(
    panel.getByRole("heading", { name: "最终解析结果" }),
  ).toBeVisible();
  const viewer = panel.locator(".json-viewer").first();
  const code = viewer.getByRole("textbox", { name: "JSON 代码查看器" });
  await expect(viewer.locator(".cm-lineNumbers")).toBeVisible();
  await expect(code).not.toHaveAttribute("contenteditable", "true");
  await expect(
    viewer.locator(".cm-line").filter({ hasText: '"items"' }).locator("span"),
  ).not.toHaveCount(0);
  await code.click();
  await page.keyboard.type("SHOULD_NOT_EDIT");
  await expect(code).not.toContainText("SHOULD_NOT_EDIT");
  await viewer.getByRole("button", { name: "折叠代码", exact: true }).click();
  await expect(viewer.locator(".cm-foldPlaceholder").first()).toBeVisible();
  await viewer.getByRole("button", { name: "展开代码", exact: true }).click();
  await expect(viewer.locator(".cm-foldPlaceholder")).toHaveCount(0);
  await viewer.getByRole("button", { name: "自动换行", exact: true }).click();
  await expect(code).toHaveClass(/cm-lineWrapping/);
  await viewer.getByRole("button", { name: "自动换行", exact: true }).click();
  await viewer.getByRole("button", { name: "查找", exact: true }).click();
  const search = viewer.locator('.cm-search input[name="search"]');
  await search.fill("compound_229");
  await search.dispatchEvent("change");
  await search.press("Enter");
  await expect(code).toContainText("compound_229");
  await search.fill("END");
  await search.dispatchEvent("change");
  await search.press("Enter");
  await expect(code).toContainText("END");
  await search.fill("[redacted]");
  await search.dispatchEvent("change");
  await search.press("Enter");
  await expect(code).toContainText("[redacted]");
  await expect(code).not.toContainText("must-not-appear");
  await search.press("Escape");
  const downloadPromise = page.waitForEvent("download");
  await panel.getByRole("link", { name: "下载结果 JSON" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("p000.g000-wd_done-result.json");
  const { readFile } = await import("node:fs/promises");
  const downloaded = JSON.parse(
    await readFile((await download.path())!, "utf8"),
  );
  expect(downloaded.extraction.items).toHaveLength(230);
  expect(downloaded.extraction.description).toBe("A".repeat(17000) + "END");
});

test("figure evidence view shows gaps, applied changes, budgets and independently labelled previews", async ({
  page,
}) => {
  await mock(page);
  const gap = {
    gap_id: "members",
    question: "编号与取代基如何对应",
    status: "resolved",
    field_paths: ["/members"],
    evidence_ids: ["e2"],
    note: "高清表格已核对",
    changes: { "/members": { before: [], after: ["1", "2"] } },
  };
  const events = [
    {
      kind: "context",
      active_images: 3,
      request_bytes: 2048,
      remaining: { tools: 12, visual_reads: 5, extractions: 2 },
      gaps: [gap],
    },
    {
      kind: "tool",
      name: "preview_pages",
      arguments: { reason: "浏览候选页，选择表格" },
      result: {
        image_refs: [
          { blob_ref: "same-preview", page: 3 },
          { blob_ref: "same-preview", page: 5 },
        ],
      },
    },
    {
      kind: "stopped",
      reason: "agent_finished",
      status: "completed",
      gaps: [gap],
      applied_evidence_ids: ["e2"],
      unapplied_evidence: ["e3"],
    },
  ].map((data) => ({
    stage: "structure",
    kind: "agent_exploration",
    at: "2026-11-22",
    data,
  }));
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs/wd_old"))
      return route.fulfill({
        json: {
          run: {
            id: "wd_old",
            status: "completed",
            steps: [{ stage: "structure", status: "completed" }],
            correction_version: 0,
            draft_version: 0,
          },
          states: { structure: { exploration: { gaps: [gap] } } },
          events,
        },
      });
    if (path.endsWith("/blobs/same-preview"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="150"><rect width="100" height="150" fill="white"/></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=workflow");
  await page.getByRole("button", { name: /结构化提取 ·/ }).click();
  const panel = page.getByRole("region", { name: "图表 Agent 探索过程" });
  await expect(panel.getByText("问题与证据 · 1 个信息缺口")).toBeVisible();
  await panel.getByText("问题与证据 · 1 个信息缺口").click();
  await expect(panel.getByText(/当前请求 3 张图像/)).toBeVisible();
  await panel.getByText("已应用并校验 · 编号与取代基如何对应").click();
  await expect(panel.getByText("影响字段：/members")).toBeVisible();
  await expect(
    panel.getByText("高清表格已核对", { exact: true }),
  ).toBeVisible();
  await expect(panel.locator(".json-viewer").first()).toBeVisible();
  await expect(panel.getByText("已用于当前结果：e2。")).toBeVisible();
  await expect(panel.getByText("尚未应用：e3。")).toBeVisible();
  await expect(
    panel.getByText("PDF 第 4 页 · 页面概览，需高清核对"),
  ).toBeVisible();
  await expect(
    panel.getByText("PDF 第 6 页 · 页面概览，需高清核对"),
  ).toBeVisible();
  await expect(panel.getByAltText(/Agent 页面概览/)).toHaveCount(2);
});

test("Agent process follows audit, preview, selected detailed image, local patch and re-audit in operation order", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_trace",
    status: "needs_review",
    created_at: "2026-11-23",
    steps: [{ stage: "structure", status: "completed" }],
    correction_version: 0,
    draft_version: 0,
  };
  const events = [
    {
      kind: "structure_review",
      status: "needs_revision",
      review_id: "sr1",
      issues: [
        {
          issue_id: "i1",
          description: "成员取代基尚未补齐",
          field_path: "/pathways/0/steps/0/products/0/variants",
        },
      ],
    },
    {
      kind: "context",
      turn: 1,
      active_images: 1,
      review_id: "sr1",
      image_refs: [{ blob_ref: "target-image", page: 0 }],
    },
    {
      kind: "decision",
      turn: 1,
      message: "先浏览第3、4页概览，定位成员表格。",
      tools: ["preview_pages"],
    },
    {
      kind: "tool_start",
      turn: 1,
      call_id: "c1",
      name: "preview_pages",
      arguments: { pages: [2, 3], reason: "根据审核意见定位成员定义" },
    },
    {
      kind: "tool",
      turn: 1,
      call_id: "c1",
      name: "preview_pages",
      result: {
        ok: true,
        evidence_id: "e1",
        image_refs: [
          { blob_ref: "preview-3", page: 2 },
          { blob_ref: "preview-4", page: 3 },
        ],
      },
    },
    {
      kind: "context",
      turn: 2,
      active_images: 3,
      image_refs: [
        { blob_ref: "target-image", page: 0 },
        { blob_ref: "preview-3", page: 2, precision: "preview" },
        { blob_ref: "preview-4", page: 3, precision: "preview" },
      ],
    },
    {
      kind: "decision",
      turn: 2,
      message: "根据概览选择第4页的Table A，核对后只补缺失字段。",
      tools: ["inspect_visual", "patch_structure"],
    },
    {
      kind: "tool_start",
      turn: 2,
      call_id: "c2",
      name: "inspect_visual",
      arguments: {
        node_id: "doc_a/r1/p003.g000",
        reason: "细看Table A的取代基列",
      },
    },
    {
      kind: "tool",
      turn: 2,
      call_id: "c2",
      name: "inspect_visual",
      duration_ms: 1200,
      result: {
        ok: true,
        evidence_id: "e2",
        node_ref: "doc_a/r1/p003.g000",
        image_refs: [{ blob_ref: "table-a-detail", page: 3, label: "Table A" }],
      },
    },
    {
      kind: "tool_start",
      turn: 2,
      call_id: "c3",
      name: "patch_structure",
      arguments: { reason: "只补齐缺失成员，不重新生成整图" },
    },
    {
      kind: "structure_review",
      turn: 2,
      parent_call_id: "c3",
      status: "passed",
      review_id: "sr2",
      issues: [],
    },
    {
      kind: "tool",
      turn: 2,
      call_id: "c3",
      name: "patch_structure",
      result: {
        ok: true,
        patch: {
          changes: [
            {
              path: "/pathways/0/steps/0/products/0/variants",
              before: [],
              after: [{ reference: "L-a" }],
            },
          ],
        },
        structure_review: { status: "passed" },
      },
    },
    {
      kind: "tool_start",
      turn: 2,
      call_id: "c4",
      name: "inspect_page",
      arguments: { page: 5 },
    },
    {
      kind: "tool",
      turn: 2,
      call_id: "c4",
      name: "inspect_page",
      result: { ok: false, error: "page render failed" },
    },
    { kind: "stopped", status: "needs_review", reason: "agent_finished" },
  ].map((data) => ({
    kind: "agent_exploration",
    stage: "structure",
    at: "2026-11-23T01:00:00Z",
    data,
  }));
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_trace"))
      return route.fulfill({
        json: {
          run,
          states: { structure: { exploration: { evidence: [] } } },
          events,
        },
      });
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="white"/><text x="20" y="80">Evidence</text></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  await page.getByRole("button", { name: /结构化提取 ·/ }).click();
  const trace = page.locator(".agent-timeline");
  const cards = trace.locator(".agent-process-step");
  await expect(cards).toHaveCount(8);
  await expect(
    trace.locator(".agent-round, .agent-viewed-sources"),
  ).toHaveCount(0);
  expect(await cards.locator(":scope > header h4").allTextContents()).toEqual([
    "独立审核结构化结果",
    "第 1 轮 · Agent 选择下一步操作",
    "第 1 轮 · 浏览页面概览",
    "第 2 轮 · Agent 选择下一步操作",
    "第 2 轮 · 查看图表高清图",
    "第 2 轮 · 局部修改结构化结果",
    "第 2 轮 · 查看整页",
    "本次处理结束",
  ]);
  await expect(cards.nth(0)).toContainText("成员取代基尚未补齐");
  await expect(cards.nth(1)).toContainText("本次实际附带的图像 · 1 张");
  await expect(cards.nth(2).locator("img")).toHaveCount(2);
  await expect(cards.nth(3)).toContainText("根据概览选择第4页的Table A");
  await expect(cards.nth(4).locator("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/table-a-detail",
  );
  await expect(cards.nth(5)).toContainText("本次修改的字段");
  await expect(cards.nth(5).locator(".agent-operation-children")).toContainText(
    "通过",
  );
  await expect(cards.nth(6)).toContainText("page render failed");
  await cards
    .nth(4)
    .getByRole("button", { name: "放大查看 PDF 第 4 页" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/table-a-detail",
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "放大", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("125%");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await trace.screenshot({ path: "/tmp/figure-agent-operation-flow.png" });
});

test("controlled regeneration shows reason, archived version and nested independent review", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_regen",
    status: "needs_review",
    created_at: "2026-11-23",
    steps: [{ stage: "structure", status: "completed" }],
    correction_version: 0,
    draft_version: 0,
  };
  const events = [
    {
      kind: "tool_start",
      call_id: "regen",
      name: "regenerate_structure",
      arguments: {
        expected_version: 1,
        note: "整体路线拓扑错误，需要重建主链与分支",
      },
    },
    {
      kind: "structure_review",
      parent_call_id: "regen",
      status: "passed",
      review_id: "review-new",
      issues: [],
    },
    {
      kind: "tool",
      call_id: "regen",
      name: "regenerate_structure",
      result: {
        ok: true,
        regeneration: {
          base_version: 1,
          new_version: 2,
          note: "整体路线拓扑错误，需要重建主链与分支",
          status: "reviewed",
          review_status: "passed",
        },
        structure_review: { status: "passed" },
      },
    },
  ].map((data) => ({
    kind: "agent_exploration",
    stage: "structure",
    at: "2026-11-23",
    data,
  }));
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_regen"))
      return route.fulfill({
        json: {
          run,
          events,
          states: {
            structure: {
              exploration: {
                evidence: [],
                structure_versions: [
                  {
                    version: 1,
                    extraction: { status: "unclear", pathways: [] },
                  },
                ],
              },
            },
          },
        },
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  await page.getByRole("button", { name: /结构化提取 ·/ }).click();
  const trace = page.locator(".agent-timeline");
  await expect(trace.locator(".agent-process-step")).toHaveCount(1);
  await expect(trace).toContainText("整体重建结构化结果");
  await expect(trace).toContainText("整体路线拓扑错误，需要重建主链与分支");
  await expect(trace).toContainText("版本 1 → 2");
  await expect(trace.locator(".agent-operation-children")).toContainText(
    "通过",
  );
  await expect(trace).toContainText("重建前的历史版本 · 1 份");
});

test("restored evidence is labelled separately and edit repair remains visible", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_workflow",
    status: "needs_review",
    created_at: "2026-11-23",
    steps: [{ stage: "structure", status: "completed" }],
    correction_version: 0,
    draft_version: 0,
  };
  const events = [
    {
      kind: "evidence_restored",
      image_refs: [
        { blob_ref: "saved-picture", page: 9, node_ref: "doc_a/r1/p009.g000" },
      ],
      omitted_evidence_ids: ["later"],
    },
    {
      kind: "context",
      active_images: 1,
      image_refs: [{ blob_ref: "saved-picture", page: 9 }],
      workflow: {
        mode: "close_pending_work",
        pending_edits: [{ edit_id: "edit1", status: "needs_correction" }],
        remaining: { turns: 2 },
      },
    },
    { kind: "decision", tools: ["preview_reference_assignments"] },
    {
      kind: "tool_start",
      call_id: "preview",
      name: "preview_reference_assignments",
      arguments: {},
    },
    {
      kind: "tool",
      call_id: "preview",
      name: "preview_reference_assignments",
      result: {
        ok: false,
        error: "invalid assignment",
        repair: {
          code: "missing_attachment_values",
          path: "/assignments/0/substituents",
          missing_keys: ["Site-K"],
        },
      },
    },
    {
      kind: "stopped",
      status: "needs_review",
      reason: "no_progress",
      unfinished_work: { pending_edits: [{ edit_id: "edit1" }] },
    },
  ].map((data) => ({
    kind: "agent_exploration",
    stage: "structure",
    at: "2026-11-23",
    data,
  }));
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_workflow"))
      return route.fulfill({
        json: {
          run,
          events,
          states: { structure: { exploration: { evidence: [] } } },
        },
      });
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="white"/></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  await page.getByRole("button", { name: /结构化提取 ·/ }).click();
  const trace = page.locator(".agent-timeline");
  await expect(trace).toContainText("恢复已保存的参考图");
  await expect(trace).toContainText("没有重新调用图像查阅工具");
  await expect(trace).toContainText("当前待完成工作与剩余预算");
  await expect(trace).toContainText("所选连接点缺少完整基团值：Site-K");
  await expect(trace).toContainText("未完成工作与交接清单");
  await trace
    .locator(".agent-process-step")
    .first()
    .getByRole("button")
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/saved-picture",
  );
});

test("source review reports a member field and opens its exact reference image", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_source_review",
    status: "needs_review",
    created_at: "2026-11-23",
    steps: [{ stage: "structure", status: "completed" }],
    correction_version: 0,
    draft_version: 0,
  };
  const events = [
    {
      kind: "tool_start",
      call_id: "submit",
      name: "record_reference_assignments",
      arguments: {},
    },
    {
      kind: "reference_review",
      parent_call_id: "submit",
      status: "needs_revision",
      image_refs: [{ blob_ref: "source-check", page: 8 }],
      issues: [
        {
          assignment_index: 2,
          field: "position_definitions",
          description: "来源位置列未完整保留",
          source_evidence: "参考表格中包含两个位置列",
          suggested_action: "仅补齐缺少的位置字段",
        },
      ],
    },
    {
      kind: "tool",
      call_id: "submit",
      name: "record_reference_assignments",
      result: { ok: false, error: "source review needs correction" },
    },
  ].map((data) => ({
    kind: "agent_exploration",
    stage: "structure",
    at: "2026-11-23",
    data,
  }));
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_source_review"))
      return route.fulfill({
        json: {
          run,
          events,
          states: { structure: { exploration: { evidence: [] } } },
        },
      });
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="white"/></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  await page.getByRole("button", { name: /结构化提取 ·/ }).click();
  const trace = page.locator(".agent-timeline");
  await expect(trace.locator(".agent-process-step")).toHaveCount(1);
  await expect(trace).toContainText("核对参考图中的成员赋值");
  await expect(trace).toContainText("提案成员第 3 项");
  await expect(trace).toContainText("参考表格中包含两个位置列");
  await trace.getByRole("button", { name: /PDF 第 9 页/ }).click();
  await expect(page.getByRole("dialog").getByRole("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/source-check",
  );
});

test("final overview keeps route references short and offers separate intermediate files", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_summary",
    created_at: "2026-11-24T00:00:00Z",
    status: "needs_review",
    start: "deliver",
    node_ref: ref,
    steps: [
      { stage: "report", status: "reused" },
      { stage: "deliver", status: "completed" },
    ],
  };
  const result = {
    schema_version: "flowchart-summary/1.1",
    status: "needs_review",
    pathways: [
      {
        steps: [
          {
            step: 1,
            relationship_type: "optimization",
            reactants: [{ reference: "base", smiles: "CCO" }],
            products: [
              {
                reference: "family-A",
                markush: { variable_options: { R: ["Me", "Et"] } },
                variants: [
                  { reference: "1", smiles: "CCOC" },
                  { reference: "2", smiles: "CCOCC" },
                ],
              },
            ],
            conditions: null,
          },
        ],
      },
    ],
  };
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_summary/result"))
      return route.fulfill({ json: result });
    if (path.endsWith("/workbench/runs/wd_summary"))
      return route.fulfill({ json: { run, states: {}, events: [] } });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=workflow");
  const panel = page.locator(".result-file");
  await expect(
    panel.getByRole("heading", { name: "最终解析结果" }),
  ).toBeVisible();
  await expect(panel).toContainText("精简过程结果");
  await expect(
    panel.getByRole("link", { name: "下载完整化学信息" }),
  ).toHaveAttribute("href", /view=delivery/);
  await expect(
    panel.getByRole("link", { name: "下载详细中间文件" }),
  ).toHaveAttribute("href", /view=full/);
  await expect(
    panel.getByRole("textbox", { name: "JSON 代码查看器" }),
  ).toContainText("reactants");
  await expect(panel).toContainText("全部实际成员");
});

test("stage results explain each step and keep inherited snapshots and raw details distinct", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_visual",
    status: "completed",
    created_at: "2026-11-24",
    correction_version: 0,
    draft_version: 0,
    steps: stages.map((s) => ({
      stage: s.id,
      status: "reused",
      source_run: "wd_source",
    })),
  };
  const classification = {
    image_type: "synthetic_scheme_single",
    confidence: 0.98,
    has_chemical_structures: true,
    has_markush_scaffold: true,
    panel_count: 1,
    needs_review: false,
    evidence: ["Connected structures and reaction arrows"],
  };
  const box = {
    local_bbox: { x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.6 },
    parent_region_index: 0,
    reading_index: 0,
  };
  const structure = {
    canonical_smiles: "CCO",
    formula: "C2H6O",
    validation: { valid: true },
    api_key: "private-value",
  };
  const image_products = [
    { stage: "original", segment: 0, page: 0, blob_ref: "source-image" },
    {
      stage: "model_input_numbered",
      segment: 0,
      page: 0,
      blob_ref: "numbered-image",
    },
  ];
  const states = {
    classify: { classification },
    plan: {
      classification,
      chain: ["molecule_detect", "molecule_recognize", "scheme_extract"],
    },
    detect: { image_products },
    recognize: {},
    som: { image_products },
    post: {
      molecular_coverage: { status: "needs_review" },
      material: { backfill_errors: ["Unknown molecule 9"], measurements: [] },
      backfilled_extraction: { molecule_objects: [structure] },
    },
    check_l1: {
      checks: [
        {
          code: "C_NO_BOX",
          severity: "warning",
          field_path: "molecules",
          message: "Missing molecule region",
        },
      ],
      checklist: [],
      unchecked: [],
    },
    check_l2: {
      review: {
        items: [
          {
            verdict: "unsure",
            field_path: "steps[0]",
            reason: "Arrow is unclear",
          },
        ],
      },
    },
    report: {
      report: {
        status: "needs_review",
        open_questions: ["Confirm reaction arrow"],
        checks: [],
        unchecked: [],
      },
    },
    deliver: {
      summary_result: {
        status: "needs_review",
        pathways: [{ steps: [{ step: 1, description: "Reaction summary" }] }],
      },
    },
  };
  const events = [
    {
      stage: "detect",
      kind: "skill_result",
      data: {
        name: "molecule_detect",
        payload: { boxes: [box] },
        trace: { status: "completed", cached: true, model_calls: 0 },
      },
    },
    {
      stage: "recognize",
      kind: "skill_result",
      data: {
        name: "molecule_recognize",
        payload: { structures: [structure] },
      },
    },
  ];
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs"))
      return route.fulfill({
        json: [run, { ...run, id: "wd_compare", steps: [] }],
      });
    if (path.endsWith("/workbench/runs/wd_visual"))
      return route.fulfill({ json: { run, states, events: [] } });
    if (path.endsWith("/workbench/runs/wd_compare"))
      return route.fulfill({
        json: {
          run: { ...run, id: "wd_compare", steps: [] },
          states: {
            classify: {
              classification: {
                ...classification,
                image_type: "data_table",
                confidence: 0.5,
              },
            },
          },
          events: [],
        },
      });
    if (path.endsWith("/workbench/runs/wd_source"))
      return route.fulfill({
        json: {
          run: {
            ...run,
            id: "wd_source",
            steps: stages.map((s) => ({
              stage: s.id,
              status: "reused",
              source_run: "wd_origin",
            })),
          },
          states,
          events: [],
        },
      });
    if (path.endsWith("/workbench/runs/wd_origin"))
      return route.fulfill({
        json: {
          run: {
            ...run,
            id: "wd_origin",
            steps: stages.map((s) => ({ stage: s.id, status: "completed" })),
          },
          states,
          events,
        },
      });
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="white"/></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  const panel = page.locator(".stage-result").first();
  await expect(panel).toContainText("单条合成路线");
  await expect(panel).toContainText("98%");
  if (process.env.WORKBENCH_SCREENSHOT_DIR)
    await panel.screenshot({
      path:
        process.env.WORKBENCH_SCREENSHOT_DIR +
        "/stage-result-classification.png",
    });
  await expect(panel).toContainText("Connected structures and reaction arrows");
  await expect(panel).toContainText("本次未重新执行此步骤");
  await expect(panel.locator(".json-viewer")).not.toBeVisible();
  await page.getByLabel("对比运行", { exact: true }).selectOption("wd_compare");
  const compared = page.getByRole("region", {
    name: "对比运行结果",
    exact: true,
  });
  await expect(compared).toContainText("数据表格");
  await expect(compared).toContainText("50%");
  await expect(panel).toContainText("98%");
  await page.getByLabel("对比运行", { exact: true }).selectOption("");
  await page.getByRole("button", { name: /^Skill 路由 ·/ }).click();
  await expect(panel.locator(".stage-route li")).toHaveCount(3);
  await expect(panel).toContainText("提取合成路线");
  await page.getByRole("button", { name: /^分子检测 ·/ }).click();
  await expect(panel.locator(".stage-detection-box")).toHaveCount(1);
  await expect(panel.getByRole("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/source-image",
  );
  await panel.getByText("本步骤实际 Skill 调用 · 1 次").click();
  await expect(panel).toContainText("使用缓存结果");
  if (process.env.WORKBENCH_SCREENSHOT_DIR)
    await panel.screenshot({
      path:
        process.env.WORKBENCH_SCREENSHOT_DIR + "/stage-result-detection.png",
    });
  await page.getByRole("button", { name: /^分子识别 ·/ }).click();
  await expect(panel).toContainText("C2H6O");
  await expect(panel).toContainText("[redacted]");
  await expect(panel).not.toContainText("private-value");
  await expect(panel.locator(".stage-molecule")).toHaveCount(1);
  await page.getByRole("button", { name: /^编号图 ·/ }).click();
  await expect(panel.getByRole("img")).toHaveAttribute(
    "src",
    "/api/v1/blobs/numbered-image",
  );
  await page.getByRole("button", { name: /^回填与映射 ·/ }).click();
  await expect(panel).toContainText("Unknown molecule 9");
  await page.getByRole("button", { name: /^确定性检查 ·/ }).click();
  await expect(panel).toContainText("Missing molecule region");
  await page.getByRole("button", { name: /^模型复核 ·/ }).click();
  await expect(panel).toContainText("不确定");
  await expect(panel).toContainText("Arrow is unclear");
  await page.getByRole("button", { name: /^图表报告 ·/ }).click();
  await expect(panel).toContainText("待复核");
  await expect(panel).toContainText("Confirm reaction arrow");
  await page.getByRole("button", { name: /^精简过程结果 ·/ }).click();
  await expect(panel).toContainText("Reaction summary");
  await panel.getByText("此步骤完成后的状态与结果 · 原始 JSON").click();
  await expect(panel.locator(".json-viewer")).toBeVisible();
  await page.getByRole("button", { name: /^结构化提取 ·/ }).click();
  await expect(panel).toContainText("此步骤暂无保存的结果");
});

test("call process shows every round, full inputs and returns while protocol JSON stays folded", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_calls",
    status: "completed",
    created_at: "2026-11-24",
    steps: [{ stage: "classify", status: "completed" }],
    correction_version: 0,
    draft_version: 0,
  };
  const event = (kind: string, data: unknown) => ({
    stage: "classify",
    kind,
    at: "2026-11-24T01:00:00Z",
    data,
  });
  const events = [
    event("skill_start", {
      name: "visual_classify",
      inputs: { goal: "判断此图" },
    }),
    event("model_request", {
      task: "visual_classify",
      model: "test-model",
      request: { messages: [{ role: "user", content: "第一轮输入内容" }] },
    }),
    event("model_response", {
      content: "需要先搜索参考信息",
      tool_calls: [
        {
          id: "search-1",
          name: "search_text",
          arguments: { query: "compound" },
        },
      ],
    }),
    event("tool_request", {
      name: "search_text",
      arguments: { query: "compound", reason: "查找编号依据" },
    }),
    event("tool_response", {
      name: "search_text",
      result: {
        hits: Array.from({ length: 205 }, (_, i) => ({
          text: `命中内容-${i}`,
        })),
        text: "A".repeat(17000) + "完整文字末尾",
        api_key: "hidden-api-key",
      },
    }),
    event("tool_request", { name: "inspect_page", arguments: { page: 2 } }),
    event("tool_error", {
      name: "inspect_page",
      error: "页面读取失败，请重试",
    }),
    event("model_request", {
      task: "visual_classify",
      model: "test-model",
      request: { messages: [{ role: "user", content: "第二轮输入内容" }] },
    }),
    event("model_response", {
      content: JSON.stringify({
        conclusion: "第二轮分类结论",
        evidence: ["已核对参考信息"],
      }),
    }),
    event("tool_request", {
      name: "validate_result",
      arguments: { target: "classification" },
    }),
    event("skill_result", {
      name: "visual_classify",
      payload: { image_type: "data_table" },
    }),
  ];
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_calls"))
      return route.fulfill({
        json: {
          run,
          states: {
            classify: { classification: { image_type: "data_table" } },
          },
          events,
        },
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  const process = page.getByRole("region", {
    name: "步骤调用过程",
    exact: true,
  });
  const cards = process.locator(".invocation-card");
  await expect(cards).toHaveCount(6);
  await expect(cards.nth(1)).toContainText("第 1 轮 · 模型调用");
  await expect(cards.nth(1)).toContainText("第一轮输入内容");
  await expect(cards.nth(1)).toContainText("需要先搜索参考信息");
  await expect(cards.nth(2)).toContainText("查找编号依据");
  await expect(cards.nth(2)).toContainText("命中内容-204");
  await expect(cards.nth(2)).toContainText("完整文字末尾");
  await expect(
    cards.nth(2).locator(".invocation-output .process-content-list > li"),
  ).toHaveCount(205);
  await expect(process).not.toContainText("hidden-api-key");
  await expect(cards.nth(3)).toContainText("调用失败");
  await expect(cards.nth(3)).toContainText("页面读取失败，请重试");
  await expect(cards.nth(4)).toContainText("第 2 轮 · 模型调用");
  await expect(cards.nth(4)).toContainText("第二轮分类结论");
  await expect(cards.nth(5)).toContainText("未记录到返回");
  await expect(process.locator(".json-viewer")).toHaveCount(0);
  await cards.nth(2).getByText("原始请求体 JSON", { exact: true }).click();
  await expect(cards.nth(2).locator(".json-viewer")).toBeVisible();
  await cards.nth(2).getByText("原始请求体 JSON", { exact: true }).click();
  await expect(cards.nth(2).locator(".json-viewer")).toHaveCount(0);
});

test("recognition binds each detected molecule to its crop and SMILES; extraction distinguishes numbered input from original", async ({
  page,
}) => {
  await mock(page);
  const run = {
    id: "wd_crops",
    status: "completed",
    created_at: "2026-11-24",
    correction_version: 0,
    draft_version: 0,
    steps: [
      { stage: "recognize", status: "completed" },
      { stage: "structure", status: "completed" },
    ],
  };
  const event = (stage: string, kind: string, data: unknown) => ({
    stage,
    kind,
    at: "2026-11-24",
    data,
  });
  const events = [
    event("detect", "skill_result", {
      name: "molecule_detect",
      payload: {
        image_refs: ["bbox-source-original"],
        boxes: [
          {
            local_id: ref.split("/").at(-1) + ".m007",
            reading_index: 2,
            local_bbox: box,
            parent_region_index: 0,
          },
          {
            local_id: ref.split("/").at(-1) + ".m008",
            reading_index: 9,
            local_bbox: box,
            parent_region_index: 0,
          },
        ],
      },
    }),
    event("recognize", "skill_start", {
      name: "molecule_recognize",
      target: ref + ".m007",
      images: [{ blob_ref: "wrong-whole-image" }],
    }),
    event("recognize", "tool_request", {
      name: "molparser.recognize",
      arguments: { image_refs: ["actual-crop"] },
    }),
    event("recognize", "tool_response", {
      name: "molparser.recognize",
      result: { results: [{ input_index: 0, esmi: "CCO" }] },
    }),
    event("recognize", "skill_result", {
      name: "molecule_recognize",
      payload: {
        structures: [
          {
            node_ref: ref + ".m007",
            canonical_smiles: "CCO",
            formula: "C2H6O",
            validation: { valid: true },
          },
        ],
      },
    }),
    event("recognize", "manual_reuse", {
      node_ref: ref + ".m008",
      mol_id: 9,
      structure: {
        node_ref: ref + ".m008",
        canonical_smiles: "CCN",
        source: "manual",
      },
    }),
    event("structure", "model_request", {
      task: "figure_structure",
      request: {
        messages: [
          {
            role: "user",
            content: [
              {
                type: "text",
                text: "目标编号图 · 片段 0：以图上的分子编号对应 mol_id。",
              },
              { type: "image", blob_ref: "actual-numbered" },
              { type: "text", text: "辅助原图 · 片段 0：仅辅助核对。" },
              { type: "image", blob_ref: "actual-original" },
            ],
          },
        ],
      },
    }),
  ];
  await page.route("**/api/**", async (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname);
    if (path.endsWith("/workbench/runs")) return route.fulfill({ json: [run] });
    if (path.endsWith("/workbench/runs/wd_crops"))
      return route.fulfill({
        json: { run, states: { recognize: {}, structure: {} }, events },
      });
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="white"/></svg>',
      });
    return route.fallback();
  });
  await page.goto("/?doc=doc_a&view=figure-debug");
  await page.getByRole("button", { name: /^分子识别 ·/ }).click();
  const panel = page.getByRole("region", {
    name: "逐个分子识别过程",
    exact: true,
  });
  const cards = panel.locator(".recognition-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText("分子编号 2");
  await expect(
    cards.nth(0).getByRole("img", { name: "分子 2 的识别裁剪图", exact: true }),
  ).toHaveAttribute("src", "/api/v1/blobs/actual-crop");
  await expect(cards.nth(0)).toContainText("CCO");
  await expect(cards.nth(0)).toContainText("C2H6O");
  await expect(cards.nth(1)).toContainText("分子编号 9");
  await expect(cards.nth(1)).toContainText("沿用历史手动修正");
  await expect(cards.nth(1)).toContainText("CCN");
  await expect(cards.nth(1)).toContainText(
    "本轮沿用历史手动修正，未重新调用识别工具",
  );
  const bboxImage = cards
    .nth(1)
    .getByRole("img", { name: "分子 9 的 bbox 裁剪图", exact: true });
  await expect(bboxImage).toBeVisible();
  await expect
    .poll(() =>
      bboxImage.evaluate((im: HTMLImageElement) => [
        im.naturalWidth,
        im.naturalHeight,
      ]),
    )
    .toEqual([280, 100]);
  await expect(bboxImage).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(
    panel.locator('img[src="/api/v1/blobs/wrong-whole-image"]'),
  ).toHaveCount(0);
  await expect(panel.locator(".json-viewer")).toHaveCount(0);
  await page.getByRole("button", { name: /^结构化提取 ·/ }).click();
  const inputs = page.getByRole("region", {
    name: "结构化实际图像输入",
    exact: true,
  });
  await expect(inputs.locator("figure").first()).toContainText(
    "目标编号图 · 用于 mol_id 对应",
  );
  await expect(inputs.locator("img").nth(0)).toHaveAttribute(
    "src",
    "/api/v1/blobs/actual-numbered",
  );
  await expect(inputs.locator("figure").nth(1)).toContainText(
    "辅助原图 · 不替代编号图",
  );
  await expect(inputs.locator("img").nth(1)).toHaveAttribute(
    "src",
    "/api/v1/blobs/actual-original",
  );
  await expect(inputs.locator(".json-viewer")).toHaveCount(0);
});
