# P2：文档底座

> 周期：D2～D5 ｜ 负责人：BE-1（主）、BE-4（Agent 接入）
> 前置：P1 的 `DocumentSource`、`Tracer` 接口冻结
> 设计文档对应：2 输入分析、6.1～6.4 文档模型、6.10 过程模型、7.3 IR Builder、7.4 渲染与索引、16 数据库、15.1～15.2 API

---

## 1. 目标

把论文变成一份**可定位的文档底座**：从上传 PDF 开始，经过 MinerU 解析，构建 Document IR 写入数据库，提供页面渲染、区域裁剪、文本规范化和全文检索。完成后，P1 的 Agent Loop 改为在 IR 上工作，答案可以回跳到页面上的具体位置。

## 2. 范围

**做**

- 数据库与迁移：文档层和过程层的表（设计文档 16.1 中"文档"和"过程"两组的核心表）。
- BlobStore：本地文件系统实现，按内容寻址。
- MinerU 客户端：提交、轮询、拉取、转存、分批、路径过滤。
- IR Builder：解析 `layout.json`，处理 F1～F9 全部数据特征，用 `middle.json` 做行级增强，构建章节树、阅读顺序并做质量检查。
- 页面渲染与裁剪（PyMuPDF），文本规范化与字符偏移映射。
- 检索：PostgreSQL 全文检索、`pg_trgm`，以及覆盖正文、图注、脚注的 `grep`。
- 最小 API：上传、状态、文档树、节点、页面图、裁剪图、检索。
- Agent 接入：IR 版文档工具、Tracer 入库、Postgres checkpointer。

**不做**

图像理解、分子、Skill、用户隔离（P7 统一补上 `user_id`；本阶段表结构先预留该列，默认值为 `default`）。

## 3. 目录与模块

```text
drugpaper/
├── persistence/
│   ├── models.py                 # SQLAlchemy：documents、parse_revisions、assets、nodes、relations、runs、artifacts、artifact_dependencies
│   └── migrations/               # Alembic
├── storage/
│   ├── base.py                   # BlobStore 协议
│   └── localfs.py
├── parsers/mineru_legacy/
│   ├── client.py
│   ├── schemas.py                # layout.json / middle.json 宽松 Pydantic 模型
│   ├── adapter.py                # → IR
│   ├── line_enricher.py          # middle.json 行级增强
│   └── quality.py
├── domain/
│   ├── ids.py  geometry.py  nodes.py
├── text/
│   ├── normalize.py              # LaTeX 转写、去连字符、补空格、raw↔norm 偏移映射
│   └── index.py                  # tsv、trgm
├── render/pages.py               # 页面渲染与裁剪，按内容寻址缓存
├── docsrc/ir_source.py           # DocumentSource 的 IR 实现（替换 P1 的 layout_json）
├── tools/builtin/doc_read.py     # get_outline、get_node、get_children、get_context_window、get_section、get_page_text
├── tools/builtin/search.py       # grep、search
├── tracing/db.py                 # DbTracer：写 runs 表
└── api/
    ├── app.py
    ├── documents.py  nodes.py  search.py
```

## 4. 关键设计要点

### 4.1 IR Builder 的处理顺序

严格按设计文档 7.3 节实现，每一步都有独立的单元测试：

1. 用宽松的 Pydantic 模型解析 `layout.json`，未知字段放进 `source_ref.extra`。
2. 识别顶层单元：原始块、页内合并组、跨页合并组。
3. 按图注前缀判定合并组的类型（F2），用正则挑出真正的图注（F3）。
4. 处理成员：图注、脚注、被吞的长文本保留为段落（F4）、`figure_part`。
5. 续段处理（F5、F6）：有 `middle.json` 时按行级 bbox 确定每个 region；没有时挂到前一个非空文本块，并标记为推导所得。
6. 章节树：按全大写标题修正 `title_level`；句首粗体短句作为小标题；识别参考文献部分。
7. 前置信息：标题、作者、DOI。
8. 阅读顺序。
9. 质量检查，结果写入 `quality_flags` 和文档级质量报告。
10. 沿用现有流水线的启发式：大框套小框过滤、`fig_p{page}_{ci}` 兼容 ID。

### 4.2 坐标

- 页面坐标统一归一化到 [0,1]，同时保留原始 PDF 点坐标。
- `Geometry.regions` 支持多区域，用于续段和跨页合并图。
- 子节点坐标（P4 的分子框）使用 `local_bbox + parent_region_index`，本阶段先实现坐标换算函数和测试。

### 4.3 文本规范化与偏移映射

`text_norm` 用于检索和 grep，`text_raw` 用于展示和证据。两者之间保存字符偏移映射，grep 在 `text_norm` 上的命中可以精确映射回 `text_raw` 的位置，作为证据的 `char_span`。

### 4.4 grep 工具（完整版）

按设计文档 7.9.3 节实现输入输出结构。本阶段覆盖正文、图注、脚注、列表；表格单元格和图内文字在 P5 产生后加入。

### 4.5 Agent 接入

- `IrDocumentSource` 替换 P1 的 `LayoutJsonSource`，工具改为 `doc_read` 和 `search` 两个工具包中的工具。
- `DbTracer` 把运行记录写入 `runs` 表：`run_type`、组件、版本、输入输出、模型、token、耗时、状态、`trace_id`、`parent_run_id`。
- checkpointer 改为 `langgraph-checkpoint-postgres`（schema `lg`）。
- 答案中的引用带上页码和 region，CLI 输出可以直接定位到页面位置。

## 5. 任务分解

| # | 任务 | 负责 | 估时 |
|---|---|---|---|
| 2.1 | SQLAlchemy 模型与 Alembic 迁移；表结构预留 `user_id` 列 | BE-1 | 0.5 天 |
| 2.2 | BlobStore（LocalFS，内容寻址） | BE-1 | 0.25 天 |
| 2.3 | MinerU 客户端：分批提交、轮询、转存、过滤 `.`/`..`、服务端队列背压 | BE-1 | 0.5 天 |
| 2.4 | `layout.json` / `middle.json` 模型与解析 | BE-1 | 0.25 天 |
| 2.5 | IR Builder 第 1～4 步（单元、类型、图注、成员） | BE-1 | 0.75 天 |
| 2.6 | IR Builder 第 5 步（续段、行级增强） | BE-1 | 0.5 天 |
| 2.7 | IR Builder 第 6～10 步（章节、前置信息、顺序、质检、兼容启发式） | BE-1 | 0.5 天 |
| 2.8 | 页面渲染与裁剪缓存；坐标换算函数 | BE-1 | 0.25 天 |
| 2.9 | 文本规范化与偏移映射；FTS 与 trgm 索引 | BE-4 | 0.5 天 |
| 2.10 | grep、search 工具；doc_read 工具包 | BE-4 | 0.5 天 |
| 2.11 | DbTracer、Postgres checkpointer；Agent 切换到 IR 数据源 | BE-4 | 0.25 天 |
| 2.12 | 最小 API：`POST /documents`、`GET /documents/{id}`、`/tree`、`/nodes/{ref}`、`/pages/{p}/image`、`/nodes/{ref}/crop`、`/search` | BE-1 | 0.5 天 |
| 2.13 | 管线骨架：`ingest → mineru_parse → build_ir → render_and_index`（LangGraph 主图的前 4 个节点，本阶段同步执行） | BE-4 | 0.25 天 |

## 6. 测试

**IR Builder 单元测试（基于样例论文的真实产物，放在 `tests/fixtures/jmedchem_2c00949/`）**

| 用例 | 期望 |
|---|---|
| F1 表格无 HTML | Table 1～3 节点的 `node_type=table`，内容为图片资产 |
| F2 SAR 表被合并为 image_body | p3 合并组被判定为 `table`，标签为 "Table 1" |
| F3 图注错位 | Table 1 的标签来自成员 title 块，`"1-10"` 不作为图注 |
| F4 摘要被合并组吞掉 | 摘要段落作为独立 paragraph 节点存在，带 `absorbed_by_merge` 标记 |
| F5/F6 续段 | p0 左栏段落有 2 个 region（左栏、右栏），空文本块不成为独立节点 |
| F7 文本规范化 | `\mathrm { D } _ { 1 }` 规范化为 `D1`；grep("D1 receptor") 命中且能映射回原文 |
| F9 页眉页脚 | 进入 `discarded` 节点，不在主树中 |
| 章节树 | INTRODUCTION、RESULTS AND DISCUSSION、CONCLUSIONS、EXPERIMENTAL SECTION 识别正确 |
| 节点数量快照 | 与人工核对后的快照一致（快照文件纳入版本管理） |

**集成测试**

- 上传样例 PDF（MinerU 服务可用时）或直接导入样例产物（离线模式）→ 数据库中出现文档、节点、资产。
- API：文档树、节点详情、页面图、裁剪图、检索都返回正确结果。

**Agent 冒烟**

- `drugpaper ask --doc <document_id> "文中 Table 3 的标题是什么？"`：答案引用 Table 3 节点，带页码和区域。
- `drugpaper ask --doc <document_id> "化合物 26 在正文中第一次出现在哪里？"`：答案引用摘要段落，并给出页码。

## 7. 验收标准

- [ ] IR 单元测试全部通过；样例论文的节点快照经人工核对。
- [ ] 上传 → 解析 → IR 入库 → API 查询，全链路跑通。
- [ ] Agent 在 IR 上问答，引用可以定位到页面区域。
- [ ] `runs` 表中能查到每次 Agent 运行的完整记录；中断后可以从 checkpoint 恢复。
- [ ] 接口冻结：`DocumentNode`、`Geometry`、`Region`、`EvidenceRef`、节点 ID 规则、文档与节点 API 写入 `docs/interfaces/`。

## 8. 风险

| 风险 | 对策 |
|---|---|
| `layout.json` 在其他论文上出现样例中没有的结构（跨页合并、公式块） | 用 3～5 篇不同排版的论文补充测试；未知结构进入 `unknown` 节点，不丢数据 |
| MinerU 服务不稳定或排队 | 支持离线导入已有产物；客户端背压与重试 |
| 续段归属判断错误 | 优先使用 middle.json 行级 bbox；推导所得的 region 标记 `derived`，UI 中可见 |

## 9. 交给下一阶段的内容

- 可查询的 IR、节点 ID、坐标换算、裁剪图接口：P3、P4、P5 在此基础上生成 Artifact 和子节点。
- `artifacts`、`artifact_dependencies`、`runs` 表：P3 的 SkillRuntime 使用。
- 完整的 grep：P6 的 DocLookup 直接复用。

## 完成记录

**2026-09-30 · v0.2.0**

- 完成情况：S2.0～S2.14 全部实现。单元测试 251 个、PostgreSQL 集成测试 3 个全部通过；`scripts/verify_phase_2.py`（`make verify-p2`）的验收项全部通过。代码地图 `docs/codemap/phase-2-codemap.md`，冻结接口 `docs/interfaces/document-ir.md` 与 `openapi-p2.json`。
- 验收清单：
  - [x] IR 单元测试全部通过；样例论文快照（261 节点）经人工核对（章节树、图表标签、续段 region 逐项检查）。
  - [x] 上传 → 解析 → IR 入库 → API 查询全链路（MinerU 服务用模拟传输层返回样例产物；离线导入走真实文件）。
  - [x] Agent 在 IR 上问答，引用解析为 EvidenceRef（页码、region、char_span）——两个冒烟问题用 FakeLLM 离线脚本验证，真实模型待配置 `.env` 后运行 `verify_phase_2.py --smoke`。
  - [x] runs 表记录顶层 Run 与模型/工具/校验子 Run；PostgreSQL checkpointer 跨进程恢复（集成测试）。
  - [x] 接口冻结。
- 偏差：本机无 Docker，单元测试与本地开发用 SQLite，PostgreSQL 由嵌入式 pgserver 覆盖（无 pg_trgm，trgm 走 Python 回退；CI 用 pgvector 镜像）；偏移映射不入库，按 `norm_version` 重算；按页查询用 `nodes.page` 冗余列。详见 `CHANGELOG.md`。
- 数据上的发现：样例中合并组成员多数没有 `belong_to`（按 `merged_from` 识别）；p8 存在纯文本合并组（已解散）；Scheme 1 的图注成员混入了正文续段（标记 `caption_absorbed_text`，拆分留给后续）。
- 遗留：真实 MinerU 服务与真实模型的端到端未在本机运行；只有 1 篇样例论文，另外 2～4 篇不同排版的论文（`tests/fixtures/extra/`）待补充后做续段人工抽查；API 无鉴权（P7）；PyMuPDF 许可（AGPL）需确认。
