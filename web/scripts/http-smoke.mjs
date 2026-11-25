import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const base = process.env.SMOKE_BASE_URL || "http://127.0.0.1:5173";
const output = resolve(
  process.env.SMOKE_OUTPUT_DIR || "test-results/http-smoke",
);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
    : {}),
});
try {
  const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    }),
    errors = [],
    writes = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (!["GET", "HEAD", "OPTIONS"].includes(r.method())) writes.push(r.url());
  });
  const response = await page.request.get(`${base}/api/v1/documents`);
  expect(response.status()).toBe(200);
  const { documents } = await response.json();
  const doc = documents.find((v) => v.active_revision);
  if (!doc) throw new Error("请先使用 dev:offline 导入已有离线论文样例。");
  await page.goto(
    `${base}/?doc=${encodeURIComponent(doc.document_id)}&view=search`,
  );
  await page.getByLabel("检索内容").fill("UCM-1306");
  await page.getByRole("combobox", { name: "检索模式" }).selectOption("grep");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(page.locator(".search-result").first()).toBeVisible();
  await page
    .getByRole("button", { name: "证据 1", exact: true })
    .first()
    .click();
  await expect(page.locator("mark")).toHaveText("UCM-1306");
  await page.locator(".paper img").evaluate(async (image) => {
    if (!image.complete)
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = reject;
      });
    if (!image.naturalWidth) throw new Error("missing PDF page image");
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: resolve(output, "actual-document-evidence.png"),
  });
  for (const [nav, title] of [
    ["化合物台账", "化合物台账"],
    ["测定数据", "测定与实验条件"],
    ["图像报告", "图像理解与图表"],
    ["全文结果", "全文理解结果"],
    ["阅读检查点", "阅读过程与回查"],
    ["能力与版本", "能力与版本检查"],
  ]) {
    await page.getByRole("button", { name: new RegExp(nav) }).click();
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
  }
  await expect(page.locator(".capability-grid .card").first()).toBeVisible();
  await page.getByRole("button", { name: /请求调试台/ }).click();
  await expect(page.getByRole("heading", { name: "请求调试台" })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(output, "actual-network-debug.png") });
  expect(errors).toEqual([]);
  expect(writes).toEqual([]);
  await writeFile(
    resolve(output, "http-smoke.json"),
    JSON.stringify(
      {
        mode: "actual_http_offline_fixture_no_model",
        base,
        document_id: doc.document_id,
        revision: doc.active_revision,
        grep_evidence_highlight: true,
        page_image: true,
        read_views: true,
        capability_catalog: true,
        page_errors: errors,
        write_requests: writes,
      },
      null,
      2,
    ),
  );
  console.log(
    "实际HTTP联调通过：离线论文检索、证据原文高亮、页面图、数据视图、能力目录和调试日志；零写请求。",
  );
} finally {
  await browser.close();
}
