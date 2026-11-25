# Drugpaper 前端 Codemap

> 适用代码：`web/`（React 19 + TypeScript + Vite 6，约 6,000 行 `src/`）。
> 项目版本：0.5.0（前端包版本 0.1.0）；本文基于 2026-11-23 的仓库状态整理。
> 配套文档：[后端 Codemap](./backend-codemap.md)、`web/README.md`、根目录 `README.md`。
> 阅读约定：路径相对 `web/`；行号会漂移，定位优先用“文件 + 符号名”。

## 目录

| 章 | 内容 |
|---|---|
| [0](#sec-0) | 怎么用这份文档 |
| [1](#sec-1) | 概览（定位、技术栈、设计约定） |
| [2](#sec-2) | 运行、构建与联调 |
| [3](#sec-3) | 目录与文件表 |
| [4](#sec-4) | 应用外壳（17 个视图、URL、上传、证据跳转） |
| [5](#sec-5) | API 客户端与请求日志 |
| [6](#sec-6) | 契约与数据形状 |
| [7](#sec-7) | 通用 UI 与资源加载 |
| [8](#sec-8) | 工作台（单图执行、校正、Skill 编辑） |
| [9](#sec-9) | 运行监控 |
| [10](#sec-10) | 文档与证据 |
| [11](#sec-11) | 图像报告 |
| [12](#sec-12) | 全文结果与阅读检查点 |
| [13](#sec-13) | 化合物与测定 |
| [14](#sec-14) | 检索 / 能力 / 批次 |
| [15](#sec-15) | 受控操作与请求调试台 |
| [16](#sec-16) | Agent 轨迹回放 |
| [17](#sec-17) | 辅助模块 |
| [18](#sec-18) | 轮询、超时与取消 |
| [19](#sec-19) | 证据、坐标与 Unicode 跨端约定 |
| [20](#sec-20) | 样式与组件约定 |
| [21](#sec-21) | 测试 |
| [22](#sec-22) | 排查手册 |
| [23](#sec-23) | 扩展指南 |
| [24](#sec-24) | 前后端接口对照 |
| [25](#sec-25) | 已知限制 |
| [26](#sec-26) | 参考 |

---

<a id="sec-0"></a>
## 0. 怎么用这份文档

### 0.1 阅读路径

| 你的目的 | 建议顺序 |
|---|---|
| 第一次接手 | 第 1 章 → 第 3 章 → 第 4 章 → 第 5~7 章 → 挑一个视图看第 8 或 11 章 |
| 改工作台/单图调试 | 第 8 章 → 第 16 章（轨迹）→ 第 17 章（agent-trace）→ 第 24 章接口对照 |
| 改图表/全文展示 | 第 11、12 章 → 第 6 章（契约）→ 第 24 章 |
| 排查“页面空白/代理错误/证据跳转失败” | 第 22 章 → 第 5 章（请求日志）→ 第 19 章（证据约定） |
| 加一个新视图/接口调用 | 第 4 章 → 第 23 章 → 第 5、7 章 |

### 0.2 术语

| 术语 | 含义 |
|---|---|
| 视图（view） | 侧边栏的一个页签；由 `App` 的 `tab` 状态切换，共 17 个 |
| 工作台（Workbench） | 单图分阶段执行、暂停、校正、Skill 编辑的前端界面，对应 5 个侧边栏入口 |
| Run | 后端一次执行记录；前端可筛选、看详情、跳转 |
| Artifact / extraction | 后端结构化产物；前端多数以 JSON 展示并挂证据链接 |
| 证据（Evidence） | `{node_id, page?, char_span?, cell?}`；点击后跳到“文档与证据” |
| 快照（snapshot） | 工作台某次 run 使用的 Skill/配置/中间状态文件 |
| 本地联调版 | 不依赖真实 MinerU/模型/外部服务的前端 + 隔离后端开发模式 |

---

<a id="sec-1"></a>
## 1. 概览

### 1.1 定位

前端是**开发/诊断工作台**，不是面向最终用户的论文阅读器。它有三个目标：

1. **看**：文档树、页面图、节点区域、OCR、图表报告、全文结果、化合物/测定台账；
2. **查**：运行监控、请求调试台、节点证据跳转、Agent 轨迹回放、阶段状态对比；
3. **调**：单图分阶段执行、暂停/继承、人工分子校正、局部 Skill 编辑/校验/发布、受控写操作。

因此它刻意保持“无 UI 框架、信息密度高、原始 JSON 随处可查”的风格。

### 1.2 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 框架 | React 19 + TypeScript 5.8 | 全部函数组件 + hooks，无状态管理库 |
| 构建 | Vite 6.3 | 端口 5173，`strictPort` |
| 样式 | 原生 CSS（`src/style.css`） | 无 Tailwind/CSS-in-JS |
| JSON 查看 | CodeMirror 6（`@codemirror/*`） | 只读、折叠、搜索、行号 |
| 公式 | KaTeX 0.19 | 渲染 OCR 行中的 equation |
| 化学结构 | `@rdkit/rdkit` 2026.9.2（本地 WASM） | 不依赖外部服务，失败回退裁剪图 |
| 测试 | `node:test` + Playwright | 纯函数单测 + `page.route` mock 的 E2E |
| 格式化 | Prettier 3.10 | `format` / `format:check` |

### 1.3 关键设计约定

- **后端页码 0 起始**；界面显示 1 起始，输入/展示处显式转换。
- **坐标归一化**：后端 bbox 为页内 0–1；分子框在图表某分段内为局部 0–1，前端绘制时转换。
- **证据必须同文档同 revision**：跳转前校验，不匹配给出明确错误而不是静默失败。
- **所有请求可追踪**：统一走 `request()`，环形日志 + 脱敏 + 导出。
- **大 JSON 不手写渲染**：优先 `Raw`/`JsonViewer`，业务视图只做“摘要 + 证据 + 关键字段”。
- **失败要可见**：`ApiError`、`ResourceState`、空态、`warning-banner` 都有明确文案。

---

<a id="sec-2"></a>
## 2. 运行、构建与联调

### 2.1 npm scripts（`package.json`）

| 命令 | 实际执行 | 用途 |
|---|---|---|
| `npm run dev` | `vite --host 127.0.0.1` | 开发服务器，5173 |
| `npm run build` | `tsc --noEmit && vite build` | 类型检查 + 产物构建 |
| `npm run preview` | `vite preview --host 127.0.0.1 --port 5173` | 预览构建产物 |
| `npm test` | `node --test tests/*.test.mjs` | 纯函数单测（3 个文件） |
| `npm run test:e2e` | `playwright test` | 浏览器 E2E（3 个 spec，串行） |
| `npm run dev:offline` | `node scripts/offline.mjs` | 一键离线联调（隔离后端 + Vite） |
| `npm run test:http` | `node scripts/http-smoke.mjs` | HTTP 冒烟 |
| `npm run format` / `format:check` | Prettier | 格式化 |

### 2.2 代理与后端地址（`vite.config.ts`）

- 默认把 `/api`、`/docs`、`/openapi.json`、`/healthz` 代理到 `DRUGPAPER_API_TARGET`，未设置时 `http://127.0.0.1:18000`。
- `WORKBENCH_MOCK_API=1` 时**不配置 proxy**，供 Playwright 用 `page.route` 完全 mock。
- 端口固定 5173；被占用时直接失败（`strictPort`），避免 E2E 打到错误端口。

### 2.3 离线联调（`scripts/offline.mjs`）

1. 要求存在 `.venv/bin/python`；
2. 启动 `python -m scripts.dev_workbench --port 18001 --data-dir data/workbench-dev [--fixture tests/fixtures/jmedchem_2c00949] [--connected]`；
3. 启动 Vite（5173），注入 `DRUGPAPER_API_TARGET=http://127.0.0.1:18001`；
4. `SIGINT`/`SIGTERM` 时联动退出两个进程。

`dev_workbench.py` 使用隔离 SQLite（`workbench.db`）、独立 blobs/runs、关闭 Skill watcher、清空 Redis/Celery、`render_dpi=100`；`--connected` 只接管上游服务配置，仍用隔离库；启动时不跑模型。

### 2.4 环境变量

| 变量 | 作用 |
|---|---|
| `DRUGPAPER_API_TARGET` | Vite 代理目标 |
| `WORKBENCH_MOCK_API=1` | 关闭代理，E2E mock 模式 |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` | 指定 Chromium 可执行文件 |

---

<a id="sec-3"></a>
## 3. 目录与文件表

```
web/
├── index.html
├── package.json / package-lock.json
├── vite.config.ts / playwright.config.ts / tsconfig*.json
├── src/
│   ├── main.tsx            # 应用外壳 + 运行监控/文档视图/调试台/受控操作
│   ├── api.ts              # fetch 封装、ApiError、请求日志
│   ├── contracts.ts        # 证据/类型/文本切片/坐标校验
│   ├── ui.tsx              # Raw/Badge/useResource/ResourceState
│   ├── json-viewer.tsx     # CodeMirror JSON 只读查看器
│   ├── agent-trace.mjs     # 轨迹事件 → 操作序列（纯函数）
│   ├── debug.mjs           # 脱敏、时间线、下载
│   ├── figure-inventory.mjs# 图表节点与最新报告匹配（纯函数）
│   ├── style.css           # 全站样式
│   └── views/
│       ├── workbench.tsx   # 单图执行/校正/Skill 编辑
│       ├── agent-timeline.tsx # Agent 操作回放
│       ├── figures.tsx     # 图表报告与表格/曲线预览
│       ├── results.tsx     # 全文结果 + 阅读检查点
│       ├── compounds.tsx   # 化合物台账 + 测定
│       ├── tools.tsx       # 检索/能力目录/批次
│       ├── shared.tsx      # EvidenceLinks/Crop/Empty/Warnings
│       ├── molecule.tsx    # 本地 RDKit SVG
│       ├── ocr.tsx         # KaTeX OCR 渲染
│       └── figure-result.tsx # 流程图精简结果
├── scripts/
│   ├── offline.mjs         # 离线联调
│   └── http-smoke.mjs      # HTTP 冒烟
└── tests/
    ├── agent-trace.test.mjs / debug.test.mjs / figure-inventory.test.mjs
    └── browser/
        ├── explorer.spec.ts
        ├── stage-debug.spec.ts
        └── workbench.spec.ts
```

| 文件 | 行数（约） | 职责 |
|---|---|---|
| `src/main.tsx` | 1177 | 17 个页签的外壳；`RunMonitor`、`DocumentView`、`DebugPanel`、`Actions` |
| `src/views/workbench.tsx` | 1251 | 单图执行、阶段状态、分子校正、Skill 编辑 |
| `src/views/agent-timeline.tsx` | 647 | Agent 探索/工具/审查回放 |
| `src/views/figures.tsx` | 534 | 图表报告、表格预览、曲线预览 |
| `src/views/results.tsx` | 357 | 全文交付/审计、阅读检查点 |
| `src/views/compounds.tsx` | 341 | 化合物与测定 |
| `src/agent-trace.mjs` | 403 | 轨迹纯函数（有单测） |
| `src/views/tools.tsx` | 211 | 检索、能力、批次 |
| `src/json-viewer.tsx` | 193 | JSON 查看器 |
| `src/api.ts` | 175 | API 客户端 |
| `src/contracts.ts` | 135 | 契约 |
| `src/views/ocr.tsx` | 113 | 公式渲染 |
| `src/views/shared.tsx` | 100 | 共享小组件 |
| `src/views/molecule.tsx` | 97 | RDKit |
| `src/ui.tsx` | 86 | 资源加载与 Badge |
| `src/debug.mjs` | 77 | 脱敏/时间线 |
| `src/views/figure-result.tsx` | 67 | 流程图结果 |
| `src/figure-inventory.mjs` | 24 | 图表清单 |

---

<a id="sec-4"></a>
## 4. 应用外壳（`src/main.tsx`）

### 4.1 组件结构

| 组件 | 位置 | 作用 |
|---|---|---|
| `App` | 文件末尾 | 全局状态、侧边栏、顶栏、文档栏、视图分发、URL 同步 |
| `RunMonitor` | 28 行起 | 运行监控视图 |
| `TreeItem` | 273 行起 | 文档树递归项 |
| `DocumentView` | 302 行起 | 文档与证据视图 |
| `DebugPanel` | 545 行起 | 请求调试台 |
| `Actions` | 624 行起 | 受控写操作 |
| `navigateEvidence` | `App` 内闭包 | 证据跳转与校验 |

### 4.2 17 个视图

| tab | 侧边栏文案 | 渲染组件 | 说明 |
|---|---|---|---|
| `workflow` | 全流程工作区 | `WorkbenchView section="workflow"` | 工作台全量（步骤 + 分子 + Skill） |
| `layout` | MinerU 布局 | `WorkbenchView section="layout"` | 工作台布局/步骤模式 |
| `figure-debug` | 图表逐步解析 | `WorkbenchView section="figure-debug"` | 工作台步骤模式 |
| `molecule-debug` | 分子检测与修正 | `WorkbenchView section="molecule-debug"` | 工作台 + 分子修正 |
| `skill-editor` | 局部 Skill 编辑 | `WorkbenchView section="skill-editor"` | 工作台 + Skill 编辑 |
| `runs` | 运行监控 | `RunMonitor` | 默认视图 |
| `document` | 文档与证据 | `DocumentView` | 页面图、树、Inspector |
| `compounds` | 化合物台账 | `CompoundsView` | |
| `measurements` | 测定数据 | `MeasurementsView` | |
| `figures` | 图像报告 | `FiguresView` | |
| `search` | 论文检索 | `SearchView` | |
| `result` | 全文结果 | `ResultsView` | |
| `notes` | 阅读检查点 | `NotesView` | |
| `actions` | 调试操作 | `Actions` | 默认关闭的写操作 |
| `capabilities` | 能力与版本 | `CapabilitiesView` | 只读 |
| `batch` | 批次状态 | `BatchView` | 只能按 ID 查 |
| `debug` | 请求调试台 | `DebugPanel` | 不依赖当前文档 |

> 注意：`workflow/layout/figure-debug/molecule-debug/skill-editor` 共用**同一个** `WorkbenchView`，只是 `section` 决定显示哪些卡片；切换这些页签不会重新挂载组件（`key` 不变），状态会保留。
> `Actions` 始终以 `<div hidden={tab !== "actions"}>` 挂载，因此表单输入在切换页签后仍保留。
> 未选择文档时，除 `debug`/`capabilities`/`batch` 外都显示引导空态。

### 4.3 全局状态

| 状态 | 类型 | 作用 |
|---|---|---|
| `tab` | `string` | 当前视图；初值从 `?view=` 读取，非法值回退 `runs` |
| `docId` | `string` | 当前文档；初值从 `?doc=` 读取 |
| `poll` | `number` | 轮询间隔，1/3/10 秒，默认 3000ms |
| `paused` | `boolean` | 手动暂停轮询 |
| `hidden` | `boolean` | 页面不可见时暂停（监听 `visibilitychange`） |
| `focus` | `Evidence?` | 待跳转证据 |
| `focusRun` | `string` | 从图表/全文视图跳到某 Run |
| `navigationError` | `string` | 证据跳转失败原因 |
| `uploading` / `uploadError` | | 上传状态 |
| `parseMode` | `manual\|auto` | 上传时的解析模式 |

`interval = paused || hidden ? 0 : poll`；`useResource` 用 0 表示不轮询。

### 4.4 URL 同步

`useEffect([docId, tab])` 用 `history.replaceState` 写回 `?doc=`、`?view=`（不新增历史记录）。刷新/分享链接可恢复当前页签与文档。

### 4.5 文档列表与上传

- `useResource("/api/v1/documents", interval)`；健康检查 `/healthz`，间隔 15 秒（轮询开启时）。
- 若 `docId` 不在列表里，额外请求 `/api/v1/documents/{docId}`（`focusedDoc`），用于直接打开链接。
- 上传：`FormData` POST `/api/v1/documents?mode=manual|auto`，超时 **600 秒**；成功后 `setDocId`、切到 `workflow`、刷新列表。
- 文档栏展示状态 Badge、`ready` 提示、`active_revision`；文档 `error` 用 `pretty()` 展开。

### 4.6 证据跳转 `navigateEvidence`

```
解析 node_id：/^([^/]+)\/r(\d+)\//
  ├─ 解析失败或文档不匹配 → “该证据不属于当前文档，未跳转。”
  ├─ 无 active_revision → “当前文档尚无可核对的解析版本，请先刷新文档。”
  ├─ revision 不等于 active_revision → 提示历史 revision，请核对原始产物版本
  └─ 通过 → setFocus(e); setTab("document")
```

`DocumentView` 收到 `focus` 后选中节点、跳到 `focus.page` 或从 `node_id` 的 `pNNN.` 解析页码；节点详情返回后再校正一次页码。

### 4.7 顶栏与底部

- 刷新间隔选择、暂停/恢复按钮、轮询状态文字。
- 侧边栏底部：健康状态 Badge、`API <version>`、`/api/v1/skills` 新窗口链接、`/docs` 链接、“本地联调版 · P6 / P8 early”。

---

<a id="sec-5"></a>
## 5. API 客户端（`src/api.ts`）

### 5.1 `request<T>(path, options)`

| 参数 | 默认 | 说明 |
|---|---|---|
| `method` | `GET` | |
| `body` | — | `FormData` 原样发送且不设 `Content-Type`；其他 JSON 序列化并设 `application/json` |
| `signal` | — | 外部取消信号；与内部 `AbortController` 联动 |
| `timeout` | `30000` | 超时后 abort，并标记 `timedOut` |

行为：

1. 记录开始时间，创建 `AbortController`；
2. `fetch` → 读取 `text()` → 尝试 `JSON.parse`，失败保留字符串；
3. `!result.ok` 抛 `ApiError(status, payload)`；
4. HTTP 成功但 `content-type` 不是 JSON → 抛 `ApiError`，文案“接口未返回 JSON，请检查前端代理目标和后端路由。”；
5. `finally` 写日志并通知订阅者。

**注意**：前端中止/超时**不会停止后端任务**；写操作后应到“运行监控”核对。

### 5.2 日志 `logStore`

- 环形缓冲最近 **100** 条，新在前。
- 字段：`id`（自增）、`method`、`path`、`status`（0 表示网络层失败）、`outcome`（`success|http_error|aborted|timeout|network_error`）、`ms`、`at`（ISO）、`response`（脱敏）、`request`（脱敏；上传为 `"[PDF upload]"`）。
- 订阅接口：`subscribe(fn)`、`getSnapshot()`、`clear()`；`DebugPanel` 用 `useSyncExternalStore` 消费。

### 5.3 `ApiError`

`status` + `payload`；`message` 默认 `HTTP <status>: <redact(payload)>`。视图通常 `String(e)` 显示。

### 5.4 导出类型

| 类型 | 字段 |
|---|---|
| `Doc` | `document_id`、`title`、`filename`、`status`、`active_revision`、`quality`、`error` |
| `Run` | `id`、`document_id`、`run_type`、`component`、`component_version`、`parent_run_id`、`trace_id`、`status`、`metrics`、`error`、时间与 `duration_ms` |
| `Runs` | `runs`、`total`、`limit`、`offset`、`observed_at`、`scope` |
| `Tree` | `id`、`type`、`label?`、`text?`、`page`、`children` |
| `Node` | `node_id`、`type`、`label`、`page`、`preview?`、`regions` |

---

<a id="sec-6"></a>
## 6. 契约与数据形状（`src/contracts.ts`）

### 6.1 基础访问器

| 函数 | 行为 |
|---|---|
| `obj(v)` | 非数组对象才返回，否则 `{}` |
| `arr(v)` | 数组才返回，否则 `[]` |
| `text(v, fallback="—")` | `undefined/null/"null"` → fallback；对象 → JSON；其他 `String` |

### 6.2 证据

```ts
type Evidence = {
  node_id: string;
  page?: number;
  char_span?: [number, number];  // Unicode code point，半开区间
  cell?: [number, number];       // 表格行列
};
```

`evidence(v)`：

- 接受 `node_id` 或 `node_ref`；
- **必须**包含 `/r`（即全局引用），否则丢弃；
- 按 `[ref, char_span, cell]` 去重；
- 仅保留类型正确的 `page`/`char_span`/`cell`。

### 6.3 节点路径

- `nodePath(ref)`：`/api/v1/nodes/` + 按 `/` 分段 `encodeURIComponent`。
- `refPage(ref)`：从 `.../pNNN.` 提取页码（0 起始）；用于证据跳页。

### 6.4 文本切片（关键跨端约定）

后端 `char_span` 按 **Unicode code point** 计数；JavaScript 字符串索引按 **UTF-16 code unit**。直接用 `slice` 会在 emoji/扩展汉字上错位。因此：

```ts
const chars = Array.from(source);       // 按 code point 拆
chars.slice(0, start).join("") + 高亮 + chars.slice(end).join("")
```

`evidenceTextParts(source, span)` 返回 `[before, hit, after]`，越界/非法 span 返回 `null`（调用方回退显示原文）。

### 6.5 坐标

`normalizedBox(value)`：要求 `x0/y0/x1/y1` 都是 0–1 有限数，且 `x1>x0`、`y1>y0`，否则 `null`。

### 6.6 业务类型

| 类型 | 关键字段 |
|---|---|
| `Compound` | `compound_id`、`primary_label`、`aliases`、`role`、`role_status`、`status`、`accepted_structure_id`、`locked_fields`、`evidence`、`role_evidence` |
| `Measurement` | `measurement_id`、`compound_id`、`subject_label`、`metric`、`metric_label_raw`、`value_raw`、`unit_raw`、`value`、`unit`、`status`、`source_kind`、`assay_id`、`evidence`、`normalization_warning`、`locked` |
| `FigureReport` | `mode`、`report_id`、`node_ref`、`image_type`、`status`、`extraction`、`backfilled_extraction`、`checks`、`review`、`open_questions`、`unchecked`、`accounting`、`trace`、`run_id`、`child_reports`、`image_products` |

---

<a id="sec-7"></a>
## 7. 通用 UI 与资源加载（`src/ui.tsx`）

### 7.1 `Raw` / `JsonViewer`

- `Raw` 是 `JsonViewer` 的薄包装；所有 JSON 展示统一入口。
- `pretty(v)` 先 `redact` 再 `JSON.stringify(..., 2)`。

### 7.2 `Badge`

按状态映射颜色：

| 类别 | 状态 |
|---|---|
| 红（bad） | `failed`、`error`、`incorrect` |
| 黄（warn） | `running`、`pending`、`needs_review`、`parsing`、`warning`、`unclear`、`unsure`、`unknown`、`unchecked`、`not_determined` |
| 灰（neutral） | 其他 |

### 7.3 `useResource(path, interval)`

- `path: string | null`；`null` 表示不请求。
- 内部状态：`{key, data?, error?, loading, at?}`；切换 key 时若不同则清空旧数据；轮询刷新保留旧 `data`（避免闪烁）。
- 每次请求新建 `AbortController`；卸载/依赖变化时 abort 并清定时器。
- `refresh()` 通过版本号强制重跑（依赖数组含 `version`）。
- 返回 `{data, error, loading, at, refresh}`；若当前 state 的 key 与 path 不符，返回 `{key, loading: !!path}`。

### 7.4 `ResourceState`

- `loading` → “正在读取…”
- `error` → `role="alert"` 红色错误块

---

<a id="sec-8"></a>
## 8. 工作台（`src/views/workbench.tsx`）

工作台是前端最复杂的部分，对应 5 个侧边栏入口。理解顺序：**选图 → 执行/暂停 → 看步骤与状态 → 看分子 → 看 Skill**。

### 8.1 组件与状态

| 状态 | 作用 |
|---|---|
| `page` | 当前页（0 起始） |
| `selected` | 当前图表 `node_ref`；默认第一张 |
| `runId` | 当前查看的 run；默认最新 |
| `step` | 当前查看的阶段；默认 `classify` |
| `start` | “重跑起点”选择 |
| `stop` / `before` | 执行后/执行前暂停阶段 |
| `skill` | 指定结构化 Skill（可留空） |
| `molRef` / `smiles` | 当前分子与编辑中的 SMILES |
| `imageType` | 人工类型覆盖 |
| `draw` / `origin` / `draftBox` / `boxText` / `segment` | 绘制分子框 |
| `zoom` / `imageError` | 页面图缩放与加载失败 |
| `compareId` | 对比的历史 run |
| `busy` / `error` | 操作状态与错误 |

资源：

| 资源 | 请求 |
|---|---|
| `overview` | `GET {base}/workbench`（revision、stages、figures、counts、image_types、mode） |
| `layout` | `GET {base}/workbench/layout?page=`（页内布局节点） |
| `history` | `GET {base}/workbench/runs?node_ref=` |
| `detail` | `GET {base}/workbench/runs/{run_id}`（states/events/process_error） |
| `molecules` | `GET {base}/workbench/molecules?node_ref=` |
| `node` | `GET /api/v1/nodes/{selected}` |
| `compare` | `GET {base}/workbench/runs/{compareId}` |

自动行为：

- 未选图时自动选第一张；
- 切换图表时清空 run/对比/分子/绘制/错误；
- 没有 `runId` 时自动选最新 run；
- 选中分子时把 `e_smiles_raw`/`canonical_smiles`、局部框、分段落入编辑状态；
- 分子版本变化时把 `image_type` 同步到下拉框。

### 8.2 页面布局与选图

- 左侧页面图：`{base}/pages/{page}/image?dpi=144`；缩放 100/125/150/200%。
- 叠加两类可点击区域：
  - 布局节点（来自 `/workbench/layout`）→ 选中图表/节点；
  - 分子区域（来自 `/workbench/molecules`，排除 `removed`）→ 选中分子。
- 右侧“图表与来源”：图表下拉（显示最新状态）、`node_ref`、MinerU 原始节点 JSON。

### 8.3 单图执行与暂停

| 按钮 | 请求 |
|---|---|
| 解析此图（自动分类与路由） | `POST /runs {node_ref, start:"classify"}` |
| 仅判断类型 | `POST /runs {node_ref, stop_after:"classify"}` |
| 当前步骤后暂停 | `POST /runs/{running_id}/pause` |
| 处理未完成/问题图表 | `POST /workbench/auto` |
| 从此步骤创建新运行 | `POST /runs {node_ref, start, source_run}` |
| 继承上游并继续 | `POST /runs {node_ref, start: next_stage, source_run, ...}` |
| 沿用结果继续审查与局部修正 | `POST /runs {start:"structure", source_run, reuse_structure:true}` |

“执行范围与调试 Skill 指定”可设置 `stop_after`、`pause_before`、`skill`；`execute(from, continuing)` 在 `continuing` 时不传 stop/before。

人工类型覆盖保存后，下次运行会重新路由；当前 run 若 `correction_version < molecules.version`，显示“人工结果已更新，相关下游需要重跑”。

### 8.4 步骤、状态与对比

阶段来自 `overview.stages`（后端固定 10 个）：

`classify → plan → detect → recognize → som → structure → post → check_l1 → check_l2 → report`

- 步骤按钮显示：状态（`未执行`/`completed`/`reused`…）、`继承上游`、`duration_ms`。
- `latestState` 显示 `classification` 与 `chain`；`<details>` 展开完整状态 JSON。
- 对比历史运行：并排显示当前与另一次 run 的同一步骤状态。
- `structure` 阶段额外显示：
  - “沿用结果继续审查与局部修正”按钮；
  - `AgentEvidenceOverview`：信息缺口（`open/resolved/conflict/unresolved` 中文标签）、影响字段、证据、上下文图像数/请求字节/剩余预算、已用/未用证据；
  - `AgentTimeline`（见第 16 章）。
- `StageRecords`：该阶段原始事件（排除 `agent_exploration`），按事件展开、渲染其中的 `blob_ref` 图片、导出完整步骤记录；`structure` 阶段默认折叠以突出轨迹。

### 8.5 结果文件

当 `post`/`report` 步骤完成或复用时，加载 `FigureResultFile`：

- `endpoint = {base}/workbench/runs/{run_id}/result`
- 有 `flowchart-result/1.0` 时显示精简流程图；否则展示完整 JSON，可下载。

### 8.6 分子检测与人工修正

顶部动作（均创建 run）：

| 按钮 | body |
|---|---|
| 仅检测分子 | `{node_ref, operation:"detect"}` |
| 识别当前全部分子 | `{node_ref, operation:"recognize"}` |
| 仅重识别此分子 | `{node_ref, operation:"recognize_one", molecule_ref}` |
| 复用当前预处理并解析 | `{node_ref, source_run, reuse_molecules:true}` |

分子列表来自 `/workbench/molecules`；显示 `node_ref`、状态、是否人工（`manual_smiles`/`manual_box`）。

选中分子后：

- 分子式、E-SMILES/规范 SMILES；
- `Molecule` 本地 RDKit 结构图（SMILES 优先；失败回退 `/api/v1/nodes/{ref}/crop`）；
- SMILES 文本框；“校验并保存局部 SMILES” → `POST corrections {kind:"smiles", molecule_ref, smiles, expected_version}`；
- “在 PDF 绘制新位置 / 新增框”：进入绘制模式，指针拖拽；
- “删除框” → `{kind:"box", molecule_ref, bbox, segment, deleted:true}`；
- “恢复机器原始结果” → `{kind:"restore", molecule_ref}`；
- “精确修改框”：输入 `x0,y0,x1,y1` 与分段号，保存局部框。

绘制逻辑（`finishDraw`）：

1. 把屏幕坐标转成页内归一化坐标；
2. 必须完整落在**某个图表分段**的 bbox 内，且面积足够（宽高 > 0.002）；
3. 找到该分段 `i`，转换为该分段的**局部坐标**；
4. `POST corrections {kind:"box", molecule_ref?, segment:i, bbox:local, expected_version}`。

所有 correction 都带 `expected_version = molecules.version`，后端做乐观锁。

### 8.7 局部 Skill 编辑（`SkillEditor`）

- 仅对“当前文档 + 当前图表”生效；先保存、校验，再用单图执行试运行。
- 文件列表：`GET {base}/workbench/files?node_ref=` → `{version, files, changed}`。
- 当前文件：`GET .../files?node_ref=&path=` → `{version, content, baseline, local}`。
- 历史快照：`GET {base}/workbench/runs/{run_id}/file?path=`（该 run 实际使用的文件）。
- 路由链快捷按钮：点击把路径设为 `drugpaper/skills/builtin/{name}/SKILL.md`。
- 支持搜索文件、直接输入路径（可新增包内文件）。
- 操作：
  - 保存局部版本：`PUT /workbench/files {node_ref, path, content, expected_version}`；
  - 校验已保存包：`POST /workbench/validate`；
  - 主动更新全局：确认后 `POST /workbench/publish`。
- 下方展示与全局基线的并排 diff（`baseline` vs 当前内容）。

---

<a id="sec-9"></a>
## 9. 运行监控（`RunMonitor`）

- 请求：`GET {base}/runs?limit=50&offset=&status=&component=&trace_id=`，带轮询。
- 统计卡片：匹配记录总数、本页运行中、本页失败、最近成功读取时间。
- 筛选：
  - 状态下拉（running/completed/succeeded/failed/pending/needs_review/cached）；
  - 组件**精确匹配**输入（如 `reader_read`）；
  - Trace ID（服务端筛选）；
  - 本页搜索（Run ID / 组件 / 错误，客户端过滤）。
- 时间线：`timeline(rows)` 计算相对时间条。
- 详情：`GET {base}/runs/{run_id}`；展示 metrics、model、error、inputs/outputs、父 Run、trace 筛选；可导出 JSON。
- 支持从其他视图带 `focusRun` 直接打开某 run。

---

<a id="sec-10"></a>
## 10. 文档与证据（`DocumentView`）

### 10.1 三栏布局

| 栏 | 内容 |
|---|---|
| 左 | 文档树（`GET /tree`） |
| 中 | 页面图 + 区域叠加 |
| 右 | 节点 Inspector |

### 10.2 文档树 `TreeItem`

- 标题：`label` 或 `text` 前 45 字或 `type`；
- 页码显示 `P{item.page + 1}`；
- 子节点放在 `<details>` 内递归。

### 10.3 页面图

- `GET {base}/pages/{page}/image?dpi=`，DPI 72/100/144；缩放 75/100/150/200%。
- 页码输入 1 起始，内部 0 起始；校验整数且在页数内。
- 页数来自 `doc.quality.page_count_pdf || page_count_layout`。
- 叠加区域：`GET {base}/pages/{page}/nodes?include_discarded=`；每个 region 渲染为绝对定位按钮（百分比），`derived` 在 title 标注；`discarded` 可切换显示。
- 图片加载失败显示“页面图加载失败，请检查后端 PDF 资产与页码。”

### 10.4 Inspector

- `GET /api/v1/nodes/{selected}`（逐段编码）。
- `OcrContent` 渲染 OCR 公式。
- 有 focus 证据且节点匹配时：
  - 显示原文并用 `<mark>` 高亮 `char_span`（`evidenceTextParts`，UTF-16 安全）；
  - 若有 `cell`，显示“来源表格单元格：r, c”警告；
- `<details>` 展开完整节点 JSON。

---

<a id="sec-11"></a>
## 11. 图像报告（`src/views/figures.tsx`）

### 11.1 清单与统计

- `GET {base}/figure-reports` + `GET {base}/tree`。
- `figureInventory(root, reports)`（`figure-inventory.mjs`）：
  - 遍历树中 `table/figure/scheme`；
  - 报告按 API 顺序（新→旧）匹配，同一 `node_ref` 只取第一个，跳过 `mode=query`；
  - 返回每个图表节点 + 最新 full 报告（可能为空）。
- 统计：解析发现图表、已有提取报告、尚无报告、最新报告失败。
- 明确警告：文档 `ready` 只代表解析完成，不代表表格/曲线/分子已提取；`ok` 也不代表经过独立内容准确性验收。

### 11.2 列表

- 左栏：
  - “已发现，尚无提取报告”：可查看源图；
  - “已保存的报告”：按状态/图像类型筛选；显示检查项数、疑问数、report_id、node_ref。
- 报告状态筛选：`ok`/`remediated`/`needs_review`/`failed`。

### 11.3 报告详情

- 源图预览：`/api/v1/nodes/{node_ref}/crop` 或原图。
- 产物版本切换：
  - `extraction`：原始抽取；
  - `pre_backfill`：回填前；
  - `backfilled_extraction`：回填后。
- 按 `image_type` 渲染：
  - 表格类 → `TablePreview`；
  - 图表类 → `ChartPreview`；
  - 其他 → `Raw`。
- `TablePreview`：
  - 使用 `header_rows`/`rows`；`rowSpan`/`colSpan` 限制 1–100；
  - 单元格可点击，选中后高亮并显示证据；
  - 最多渲染前 200 行。
- `ChartPreview`：
  - `series`/`points`；log10 轴跳过非正值；
  - 简单 SVG 柱状/折线；
  - 显示坐标标定信息。
- 其余区块：检查项、复核（`review`）、`unchecked`、`accounting`、`trace`、完整报告 JSON。
- 证据按钮调用 `onEvidence` → 全局 `navigateEvidence`。
- 可跳到对应 Run（`onRun` → `navigateRun`）。

---

<a id="sec-12"></a>
## 12. 全文结果与阅读检查点（`src/views/results.tsx`）

### 12.1 `ResultsView`

- `GET {base}/full-text`；404 显示明确空态（尚未生成/版本不匹配），不伪造内容。
- 数据形状：`projection.delivery`（交付）+ `projection.audit`（审计孪生）。
- `Field` 递归渲染：
  - 深度上限 12，每层最多 200 项；
  - 叶子值若带证据，显示 `EvidenceLinks`；
  - 显示 `source_ids` 等来源标识。
- 页签：字段 / 审计 / 原始（完整 JSON）。
- 可跳到 Run。

### 12.2 `NotesView`

- `GET {base}/reader-notes?limit=50&offset=`，带轮询。
- 每条：页码、版本、fingerprint、累计页/模型调用/工具调用、warnings。
- 逐页 deltas：`thesis_updates`、`compound_updates`、`narrative_events`、`sar_observations`、`open_questions`。
- 累计 `open_questions`、`lookup_answers`。
- 完整 JSON 可展开；证据可跳转。

---

<a id="sec-13"></a>
## 13. 化合物与测定（`src/views/compounds.tsx`）

### 13.1 `CompoundsView`

- `GET {base}/compounds?limit=50&offset=`；详情 `GET {base}/compounds/{id}`。
- 列表字段：`primary_label`、别名、角色/状态、是否有采纳结构。
- 详情：
  - 本地 RDKit 结构图（accepted structure 的 SMILES）；
  - 采纳来源（artifact/结构候选）；
  - 冲突列表（`chemistry_conflicts`）；
  - 全部结构候选（active/历史）；
  - 导出 JSON。
- 证据按钮跳转文档。

### 13.2 `MeasurementsView`

- `GET {base}/measurements`、`GET {base}/assays`。
- 支持按指标/状态/来源筛选；表格展示原值、单位、归一值、状态、证据。
- 测定详情展示关联 assay（靶点、物种、体系、条件）。
- `normalization_warning`、`locked` 明确标出。

---

<a id="sec-14"></a>
## 14. 检索 / 能力 / 批次（`src/views/tools.tsx`）

### 14.1 `SearchView`

- `GET {base}/search?q=&mode=hybrid|fts|trgm|grep&literal=&limit=50`。
- 结果展示节点、snippet、score；证据可跳转。
- `literal` 用于字面匹配；`grep` 模式不依赖索引。

### 14.2 `CapabilitiesView`

- `GET /api/v1/skills/catalog?audience=chat`；可按名称/描述筛选。
- 卡片展示 `name`、`l0 || description || summary`、`l1`、完整能力卡 JSON。
- “检查注册版本” → `GET /api/v1/skills/{name}/versions`；无版本时说明“工具能力可能没有独立 Skill 版本记录”。
- 只读，不执行 Skill、不触发热加载。

### 14.3 `BatchView`

- 只能输入已有 `batch_id` 查询：`GET /api/v1/batches/{id}`，带轮询。
- 展示状态、任务数、文档数、完整 JSON。
- 后端无批次列表接口，因此不展示推测的历史批次。

---

<a id="sec-15"></a>
## 15. 受控操作与请求调试台（`main.tsx`）

### 15.1 `Actions`（调试操作）

- **默认关闭**；需勾选“启用本会话写操作”。
- 操作类型与请求体：

| 操作 | 路径 | body |
|---|---|---|
| 顺序阅读 / 指定页重读 | `POST {base}/reader` | `{from_page?}`（1→0 转换） |
| 完整全文理解 | `POST {base}/full-text` | `{prepare_text, force, from_page?}` |
| 只读内容回查 | `POST {base}/lookups` | `{questions:[{question, kind:"text"}]}` |
| 生成 ZIP 导出 | `POST {base}/exports` | `{}` |

- 超时 **600 秒**；执行前有确认弹窗；结果用 `Raw` 展示，导出结果提供 ZIP 下载。
- 失败文案强调：“请求结束不代表后端已停止，请在运行监控中核对。”
- 页码输入 1 起始，发送前转 0 起始；非法输入阻止提交。

### 15.2 `DebugPanel`（请求调试台）

- 数据来自 `logStore`，最近 100 条。
- “仅失败”过滤：`outcome !== "success"`。
- 列表显示：方法、状态或“网络”、outcome、路径、耗时、时间。
- 详情：完整日志对象（已脱敏）；“复制请求 URL”（clipboard 失败给出提示）。
- 导出脱敏请求记录 JSON；清空日志。
- 文案说明：HTTP 0 表示连接失败/超时/客户端中止；中止等待不会停止后端任务；图片请求请用浏览器 Network 查看。

---

<a id="sec-16"></a>
## 16. Agent 轨迹回放（`src/views/agent-timeline.tsx`）

### 16.1 输入

```ts
<AgentTimeline
  events={detail.events}          // 后端 run 事件（含 kind/stage/at/data）
  exploration={states.structure.exploration}
  base={base}
  running={running}
/>
```

### 16.2 组件

| 组件 | 作用 |
|---|---|
| `AgentTimeline` | 主组件：`traceSteps()` 生成操作序列并渲染 |
| `ToolOperation` | 单次工具/操作：目的、输入图像、候选来源、文字结果、证据图库、补丁变更、整体重建、子审核、返回摘要、原始参数/返回 |
| `Review` | issues/coverage/审核图像 |
| `ChangeList` | 字段级变更列表（dry_run 与实际） |
| `EvidenceImage` | 证据图（preview/detail/source 标注），点击放大 50%–300% |
| `InputImages` | 本次模型请求的输入图像 |
| `JsonDetails` | 通用折叠 JSON |

### 16.3 展示规则

- 状态中文映射：running/completed/failed/blocked/needs_review。
- 停止原因中文映射。
- 工具中文名称来自 `toolName()`（`agent-trace.mjs` 的标签表）。
- 继承的证据（inherited evidence）单独列出，**不插入**操作链。
- 可导出完整操作记录。
- 运行中显示“执行中”提示，轮询由父级 `useResource` 驱动。

---

<a id="sec-17"></a>
## 17. 辅助模块

### 17.1 `src/agent-trace.mjs`（纯函数，有单测）

| 导出 | 作用 |
|---|---|
| `TOOL_LABELS` / 工具标签 | 工具名 → 中文 |
| `rejectionReason()` | 把探索工具拒绝规则翻译成中文 |
| `callStatus()` | `running/completed/failed/blocked/needs_review` |
| `callSummary()` | 一次调用的摘要 |
| `callImages()` | 提取 `image_refs`；对 `get_page_text/get_node/get_context_window/get_section/get_children` 生成“文字来源预览”图（`viewed=false`） |
| `sourceLabels()` | 图像来源标签 |
| `traceSteps(events, exploration)` | 把 `model_request/agent_exploration/context/decision/tool_start/tool/structure_review` 还原为操作顺序；`tool_start` 与结果按 `call_id` 配对；review 挂到 `parent_call_id`；计算每次 Agent 请求的输入图像增量 |
| `requestImageRefs()` | 从 messages content 提取图片元数据（前两张 target，其余 reference） |

### 17.2 `src/debug.mjs`

- `redact(value)`：
  - 深度 16；数组/对象各最多 200 项；
  - 键名匹配 `api_key|authorization|password|secret|access_token|admin_token` → 脱敏；
  - 字符串替换 bearer/`sk-` token；
  - 单个字符串最多 16000 字符。
- `timeline(runs)`：计算相对时间条。
- `exportJSON(name, value)`：导出时仍脱敏。
- `downloadJSON(name, value)`：Blob 下载。

### 17.3 `src/figure-inventory.mjs`

`figureInventory(root, reports)`：遍历 `table/figure/scheme`，报告新→旧，同一 `node_ref` 只取首个且跳过 `mode=query`；返回 `{id, type, label, page, report?}` 列表。有单测。

### 17.4 `src/views/molecule.tsx`

- 动态加载本地 `@rdkit/rdkit` 的 JS 与 WASM（`?url` 引入），不请求外部服务。
- `get_mol(smiles)` → `get_svg(320, 200)`；失败回退 `fallback`（通常是 `/nodes/{ref}/crop`）。
- 组件卸载/切换时主动 `delete()` 释放。

### 17.5 `src/views/ocr.tsx`

- 优先使用 `node.attributes.ocr_lines`，只渲染带 equation span 的行；
- 无 lines 时：
  - `formula` 节点整体渲染；
  - 其他节点用 `MarkedText` 识别 `$$...$$`、`\[...\]`、`\(...\)`、`$...$`。
- KaTeX `throwOnError:false`、`trust:false`、限制 `maxExpand`/`maxSize`；失败保留源码。
- 展示 `ocr_render_warning`。

### 17.6 `src/json-viewer.tsx`

- CodeMirror 6 只读 JSON：行号、折叠、括号匹配、搜索（中文文案）、主题高亮、自动换行。
- 顶部按钮：复制（clipboard 失败回退 textarea/全选）、展开/折叠、查找。
- 高度随内容动态 100–480px。

### 17.7 `src/views/figure-result.tsx`

- 从 endpoint 拉取结果并轮询。
- `schema_version === "flowchart-result/1.0"` → 精简流程图结果（extraction/pathways + 元数据）；
- 其他 → 完整 JSON；提供下载。

### 17.8 `src/views/shared.tsx`

| 组件 | 作用 |
|---|---|
| `EvidenceLinks` | 证据按钮列表（去重、带页码） |
| `Crop` | `/nodes/{ref}/crop?dpi=144&pad=0`，失败重试；支持 cell 高亮 |
| `Empty` | 统一空态 |
| `Warnings` | 警告列表 |

---

<a id="sec-18"></a>
## 18. 轮询、超时与取消

| 场景 | 间隔/超时 |
|---|---|
| 全局轮询 | 1/3/10 秒可选，默认 3 秒；`paused` 或页面隐藏时为 0 |
| 健康检查 | 15 秒 |
| 普通 API | 30 秒 |
| 文档上传 | 600 秒 |
| 工作台 run 创建/correction | 120 秒 |
| 受控操作（reader/full-text/exports/lookups） | 600 秒 |

- `useResource` 用 `setTimeout` 链式轮询（上一次完成后再排下一次），避免请求堆叠。
- 所有请求在组件卸载/路径变化时 abort；但**后端任务不会因前端 abort 而停止**。
- 轮询失败保留旧数据并显示错误，不会清空页面。

---

<a id="sec-19"></a>
## 19. 证据、坐标与 Unicode 跨端约定

### 19.1 证据

| 约定 | 前端处理 |
|---|---|
| 全局引用 `{doc}/r{rev}/{local}` | `evidence()` 只接受含 `/r` 的 ref；`navigateEvidence` 校验 doc 与 revision |
| `char_span` 按 code point | `Array.from(source)` 切片 |
| `char_span` 半开区间 | `[start, end)`；`start >= end` 视为非法 |
| `cell` 为 `[row, col]` | 跳到表格节点并显示单元格警告 |
| 证据可能来自历史 run | 若 revision 不匹配，明确提示而不是跳转 |

### 19.2 坐标

| 层级 | 坐标系 | 前端处理 |
|---|---|---|
| 页面区域 | 页内 0–1 | 百分比定位 |
| 图表分段 | 页内 0–1 | 用于判断绘制框落在哪个分段 |
| 分子框 | 分段内局部 0–1 | 绘制时从页面坐标换算；精确输入直接按局部坐标 |
| 表格单元格 | 行列索引 | `TablePreview` 高亮 |
| 曲线 | 原始坐标 + 标定 | `ChartPreview` 处理 log10 与非正值 |

### 19.3 页码

- 后端 API 一律 0 起始；
- 前端展示/输入 1 起始；
- 转换点：`DocumentView` 页码输入、`Actions` 的 `from_page`、`FiguresView` 的 `page+1` 展示、`WorkbenchView` 的 `page+1` 输入、`refPage()` 解析。

### 19.4 图像

| 用途 | 路径 |
|---|---|
| 页面图 | `{base}/pages/{page}/image?dpi=` |
| 节点裁剪 | `/api/v1/nodes/{ref}/crop?dpi=144&pad=0` |
| Blob（事件图像/产物） | `/api/v1/blobs/{ref}` |
| 后端缓存 | 页面图/裁剪图使用 immutable 缓存；结果 JSON `no-store` |

---

<a id="sec-20"></a>
## 20. 样式与组件约定

- 无 UI 框架；类名集中在 `src/style.css`。
- 常用类：
  - 布局：`app`、`sidebar`、`workspace`、`doc-grid`、`workbench-grid`、`split`、`stats`、`card`、`toolbar`、`filters`、`section-head`
  - 状态：`badge bad/warn/neutral`、`error`、`warning-banner`、`muted`、`empty`
  - 图像：`paper`、`paper-scroll`、`region`、`mol-region`、`active`、`debug-image`
  - 差异：`code-diff`
- 可访问性：交互区域是 `<button>` 并带 `aria-label`；错误用 `role="alert"`；状态复制用 `role="status"`。
- 组件保持“薄”：业务逻辑尽量放纯函数（`agent-trace.mjs`、`figure-inventory.mjs`、`debug.mjs`），便于 `node:test` 覆盖。

---

<a id="sec-21"></a>
## 21. 测试

### 21.1 单元测试（`node --test tests/*.test.mjs`）

| 文件 | 覆盖 |
|---|---|
| `agent-trace.test.mjs` | 操作序列还原、call 配对、图像增量、拒绝原因/摘要 |
| `debug.test.mjs` | 脱敏边界（深度/数组/键名/token）、时间线、导出 |
| `figure-inventory.test.mjs` | 图表清单匹配、跳过 query、同节点取最新 |

### 21.2 E2E（Playwright，`page.route` 全 mock）

`playwright.config.ts`：testDir `./tests/browser`，串行，视口 1600×1000；`WORKBENCH_MOCK_API=1` 启动 dev 或 preview；baseURL `http://127.0.0.1:5173`；支持 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。

| spec | 覆盖场景 |
|---|---|
| `explorer.spec.ts` | 化合物本地结构、图表渲染/证据/步骤跳转、全文结果、空态、非 JSON 契约错误、无报告表格源图 |
| `stage-debug.spec.ts` | 自动路由、分子校正、局部 Skill、继承恢复、失败显式错误、公式渲染、回填结果 JSON 查看器、问题缺口/预算/预览、Agent 审核-预览-修补-再审核、受控重建 |
| `workbench.spec.ts` | 运行详情/筛选/暂停/请求调试、后端页码、切换文档清空详情、断连保留快照 |

E2E 的意义：在无外部服务、无模型费用的情况下锁定**交互契约**（请求体、页码、空态、错误文案、证据跳转）。

---

<a id="sec-22"></a>
## 22. 排查手册

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 页面显示“接口未返回 JSON” | Vite 代理目标错误 / 后端未启动 / 路由不存在 | 检查 `DRUGPAPER_API_TARGET`、`/healthz`、`/docs` |
| 所有请求 HTTP 0 | 后端断开、超时、代理拒绝 | 打开“请求调试台”看 outcome；确认后端端口 |
| 证据点击提示历史 revision | 证据来自旧解析 | 刷新文档/重新解析；不要跨 revision 使用 |
| 证据点击提示不属于当前文档 | 混入了其他文档的 artifact | 检查后端证据归属校验 |
| 页面图 404 | 页码越界 / PDF 资产缺失 / 渲染失败 | 切换 DPI、核对页数；看后端 Run |
| 图表列表为空 | 文档未 ready / 无图表节点 / 报告未生成 | 看文档状态与“图像报告”统计 |
| 报告 `needs_review` | 提取/审查未通过 | 进入工作台单图执行，查看检查与轨迹 |
| 工作台 run 一直 running | 子进程卡住/模型慢 | 看 run 详情 `process_error`、阶段事件；“当前步骤后暂停” |
| 分子 SMILES 保存失败 | E-SMILES 校验失败/版本冲突 | 看返回错误；刷新分子版本后重试 |
| Skill 保存 409 | `expected_version` 过期 | 刷新文件后重新保存 |
| 全文结果空态 | 未生成/版本或 fingerprint 不匹配 | 运行“完整全文理解”；核对 revision |
| 导出失败 | 投影校验阻断（V-J/V-E） | 看后端错误详情与 checks |
| 上传按钮无响应 | 文件过大/非 PDF/后端解析超时 | 看 600 秒超时与 `uploadError` |

---

<a id="sec-23"></a>
## 23. 扩展指南

### 23.1 新增一个视图

1. 在 `src/views/` 新建组件，接收 `{base, interval, onEvidence, onRun}` 等必要 props；
2. 在 `main.tsx` 的 `tab` 初值白名单、侧边栏数组、渲染分支各加一项；
3. 如需 URL 直达，保持 `?view=` 名称一致；
4. 请求统一走 `useResource`/`request`，错误用 `ResourceState`；
5. 证据用 `contracts.evidence()` 归一，点击调用 `navigateEvidence`；
6. 补 E2E mock 用例（优先覆盖空态、错误、页码、证据跳转）。

### 23.2 新增一个数据结构展示

1. 在 `contracts.ts` 增加类型与必要的守卫（不要直接 `as`）；
2. 用 `Raw` 提供完整 JSON 兜底；
3. 业务摘要只展示关键字段，证据用 `EvidenceLinks`；
4. 若字段可能缺失/为 `null`，用 `text/obj/arr` 防御。

### 23.3 新增写操作

1. 放进 `Actions` 或对应视图的受控按钮；
2. 默认关闭/需确认，明确提示可能产生费用与记录；
3. 设置足够超时（长任务 600 秒）；
4. 失败提示“请求结束不代表后端停止”，引导看运行监控；
5. 不要在前端推断后端已完成。

### 23.4 修改证据/坐标约定

1. 先确认后端语义（code point / 归一化 / 0 起始）；
2. 只改 `contracts.ts` 的集中函数（`evidenceTextParts`、`normalizedBox`、`refPage`）；
3. 更新单测与 E2E；
4. 检查所有使用点（`DocumentView`、`shared.tsx`、`figures.tsx`、`workbench.tsx`）。

---

<a id="sec-24"></a>
## 24. 前后端接口对照（前端使用面）

| 前端位置 | 方法/路径 | 用途 |
|---|---|---|
| `App` | `GET /healthz` | 健康状态 |
| `App` | `GET /api/v1/documents`、`GET /api/v1/documents/{id}` | 文档列表/详情 |
| `App` | `POST /api/v1/documents?mode=` | 上传 PDF |
| `RunMonitor` | `GET /documents/{id}/runs`、`GET .../runs/{run_id}` | 运行监控 |
| `DocumentView` | `GET /documents/{id}/tree`、`/pages/{page}/image`、`/pages/{page}/nodes`、`GET /nodes/{ref}` | 文档与证据 |
| `DebugPanel` | 无（消费 `logStore`） | 请求调试 |
| `Actions` | `POST .../reader`、`/full-text`、`/lookups`、`/exports` | 受控写操作 |
| `WorkbenchView` | `GET .../workbench`、`/layout`、`/runs`、`/runs/{id}`、`/molecules`、`/files`、`/runs/{id}/file` | 工作台 |
| `WorkbenchView` | `POST .../workbench/runs`、`/auto`、`/runs/{id}/pause`、`/corrections`、`/validate`、`/publish` | 工作台操作 |
| `WorkbenchView` | `PUT .../workbench/files` | 保存局部 Skill |
| `WorkbenchView` | `GET /nodes/{ref}`、`/nodes/{ref}/crop`、`/blobs/{ref}` | 节点/图像 |
| `FiguresView` | `GET .../figure-reports`、`/tree`、`/nodes/{ref}/crop` | 图表报告 |
| `ResultsView` | `GET .../full-text` | 全文结果 |
| `NotesView` | `GET .../reader-notes` | 阅读检查点 |
| `CompoundsView` | `GET .../compounds`、`.../compounds/{id}` | 化合物 |
| `MeasurementsView` | `GET .../measurements`、`/assays` | 测定 |
| `SearchView` | `GET .../search` | 检索 |
| `CapabilitiesView` | `GET /skills/catalog`、`/skills/{name}/versions` | 能力目录 |
| `BatchView` | `GET /batches/{id}` | 批次 |
| `FigureResultFile` | `GET .../runs/{id}/result` 或报告 result endpoint | 结果文件 |
| `Molecule` | `GET /nodes/{ref}/crop`（回退） | 结构回退图 |

> 完整后端路由见后端 Codemap 第 7 章。

---

<a id="sec-25"></a>
## 25. 已知限制

1. **开发工具定位**：界面暴露运行、校正、Skill 编辑等能力；后端无鉴权，前端也不做用户隔离，只适合本机/可信内网。
2. **无前端权限控制**：写操作仅靠“启用本会话”与确认弹窗防误触，不是安全边界。
3. **依赖后端数据完整**：证据、页码、坐标、fingerprint 均由后端提供；前端不做推断，缺失时显示空态/错误。
4. **JSON 优先**：大量页面以原始 JSON 兜底，视觉呈现有限；表格/曲线只做轻量预览。
5. **RDKit 为本地 WASM**：首次加载有体积与初始化成本；失败回退裁剪图。
6. **轮询而非推送**：长任务靠轮询；页面隐藏时暂停，回来可能看到旧快照（会很快刷新）。
7. **前端中止不等于后端取消**：超时/关闭页面不会终止后端任务。
8. **E2E 全 mock**：不验证真实后端语义，真实契约仍以 `docs/interfaces/` 与后端测试为准。

---

<a id="sec-26"></a>
## 26. 参考

- 后端 Codemap：`docs/codemap/backend-codemap.md`
- 阶段地图：`docs/codemap/phase-1..6-codemap.md`
- 冻结接口：`docs/interfaces/`
- 后端 P8 工作台说明：`docs/dev-plan/` 中的 early workbench 文档
- 离线联调：`web/scripts/offline.mjs`、`scripts/dev_workbench.py`
