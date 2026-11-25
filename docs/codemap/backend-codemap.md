# Drugpaper 后端 Codemap

> 适用代码：仓库根目录 `drugpaper/`（约 3.7 万行 Python）与 `config/`、`prompts/`、`services/`、`deploy/`、`scripts/`。
> 项目版本：0.5.0；Python 3.12（`<3.13`）。本文基于 2026-11-23 的仓库状态整理。
> 配套文档：[前端 Codemap](./frontend-codemap.md)、冻结接口 `docs/interfaces/`、阶段代码地图 `docs/codemap/phase-1..6-codemap.md`。
> 阅读约定：路径均为仓库相对路径；行号会随开发漂移，定位时优先用文件路径 + 符号名（类/函数/表名）。

## 目录

| 章 | 内容 |
|---|---|
| [0](#sec-0) | 怎么用这份文档（阅读路径、术语） |
| [1](#sec-1) | 项目总览（定位、阶段、按任务找代码） |
| [2](#sec-2) | 快速开始与运行命令 |
| [3](#sec-3) | 目录与模块总览 |
| [4](#sec-4) | 分层架构与数据流 |
| [5](#sec-5) | 领域模型（ID、几何、节点、Artifact、证据） |
| [6](#sec-6) | 持久化层（27 张表、8 个迁移、仓储） |
| [7](#sec-7) | HTTP API（应用装配、路由总表、工作台机制） |
| [8](#sec-8) | 服务装配与配置（Settings、models/routing/services/metric） |
| [9](#sec-9) | 文档解析与 IR |
| [10](#sec-10) | Agent Runtime 与工具系统 |
| [11](#sec-11) | Skill 体系（协议、加载、注册、运行时、披露、44 个内置包） |
| [12](#sec-12) | 编排与各 Agent（Document/Figure/FigureAgent/Molecule/Text/Reader/DocLookup/FullText/Export） |
| [13](#sec-13) | 事实、化学与校验 |
| [14](#sec-14) | 工作台后端 |
| [15](#sec-15) | Worker / Celery |
| [16](#sec-16) | 追踪、运行记录与监控 |
| [17](#sec-17) | 已知限制与边界 |
| [18](#sec-18) | 扩展开发指南（Recipes） |
| [19](#sec-19) | 关键不变量 |
| [20](#sec-20) | 附录：常用符号索引 |
| [21](#sec-21) | 参考 |

---

<a id="sec-0"></a>
## 0. 怎么用这份文档

### 0.1 三种阅读路径

| 你的目的 | 建议顺序 |
|---|---|
| 第一次接手，建立全局认识 | 第 1 章 → 第 3 章 → 第 4 章 → 第 5 章 → 第 9~12 章 |
| 接手某个具体功能 | 用第 1.3 节“按任务找代码” → 跳到对应专题章节 → 查阅第 7 章路由表和附录 |
| 排查线上/联调问题 | 第 16 章（Run 与追踪）→ 第 7.2 节（通用约定/错误格式）→ 第 15 章（Worker）→ 第 17 章（已知限制与安全边界） |

### 0.2 术语速查

| 术语 | 含义 |
|---|---|
| IR | Document IR，MinerU 解析产物经适配后的统一文档中间表示（节点 + 关系 + 页面信息 + 质量报告） |
| Revision | 同一文档的一次解析版本；节点引用必须带 revision，旧 revision 只读保留 |
| Node / 节点 | IR 中的结构单元（段落、图、表、分子、章节……），带几何、文本、父子关系 |
| Artifact | 某个 Skill 在某个 Work Unit 上的结构化产物版本，可缓存、可追溯、可失效 |
| Work Unit | `{skill}:{target_ref}[:suffix]`，缓存与版本的主键；同一时刻只能有一个 active Artifact |
| Skill | 可注册、可版本化、可热加载的能力包（prompt 型或 python 型） |
| Capability | 面向 Agent 的“能力卡”，支持 L0/L1/L2/L3 渐进披露与按需加载 |
| FigureAgent | 单张图/表/流程图的阶段式理解 Agent（分类 → 路由 → 检测/识别 → 结构化 → 检查 → 补救 → 报告） |
| Reader | 逐页阅读并维护 Note/Open Question 的全文理解 Agent |
| DocLookup | 批量的只读“内容回查”Agent，回答带证据的问题 |
| Facts | 从 Artifact 投影出的可查询事实（化合物、结构候选、标签、测定、冲突等） |
| Projection | 把全文理解结果投影为固定交付 JSON（legacy 形状）+ 审计孪生 |
| Workbench | P8 开发工作台，单图分阶段执行、暂停、人工校正、Skill 局部编辑与发布 |

---

<a id="sec-1"></a>
## 1. 项目总览

### 1.1 系统定位

输入一篇药物化学 PDF，输出：

1. 可引用的文档结构（页面、章节、段落、图表、分子区域、OCR 文本）；
2. 图表级结构化结果（分子检测/识别、表格单元格、曲线数据、SAR 记录、反应路线）；
3. 全文级理解结果（逐页阅读、优化叙事、优化路径、化合物台账、测定数据）；
4. 固定交付 JSON、审计孪生、ZIP 导出包，以及可回放的运行轨迹。

所有模型判断都必须落到**当前 revision 的节点/字符证据**上；证据不足时保留 `needs_review` / 未解决项，而不是编造。

### 1.2 已实现阶段

| 阶段 | 内容 | 主要入口 |
|---|---|---|
| P1 | 最小 Agent 循环：预算、工具、答案校验、追踪、CLI 问答 | `drugpaper/cli.py`、`drugpaper/agent/` |
| P2 | 文档基础：上传、MinerU 解析、IR、页面渲染、检索、节点接口 | `orchestration/pipeline_graph.py`、`api/`、`parsers/` |
| P3 | Skill 运行时与渐进披露：包加载、注册、能力卡、元工具、热加载 | `skills/`、`capabilities/`、`tools/` |
| P4 | 分子流水线：YOLO 检测、MolParser 识别、SOM 编号图、标签绑定、化合物归一 | `orchestration/molecule_pipeline.py`、`skills/molecule.py`、`chem/` |
| P5 | 图表 Agent：分类、路由、结构化、自检、渐进补救、报告落库 | `agents/figure/`、`orchestration/figure_pipeline.py` |
| P6 | 全文理解：正文事实、页包、阅读、回查、综合、路径校验、投影、导出 | `agents/reader/`、`agents/lookup/`、`orchestration/full_text_pipeline.py`、`exporters/` |
| P8（早期） | 开发工作台：单图分阶段执行、隔离进程、人工校正、Skill 编辑/校验/发布 | `api/workbench.py`、`workbench/`、`web/` |

P7（鉴权与用户隔离）尚未实现，见第 17 章。

### 1.3 按任务找代码

| 我想改…… | 先看 |
|---|---|
| 新增/修改 HTTP 接口 | `drugpaper/api/`，注册点在 `api/app.py` |
| 文档解析、节点结构、页面图 | `parsers/mineru_legacy/`、`orchestration/pipeline_graph.py`、`render/` |
| 问答 Agent 的行为/预算 | `agent/runtime.py`、`agent/qa.py`、`agent/validators.py` |
| 新增一个 Skill（prompt 型或 python 型） | `drugpaper/skills/builtin/<name>/`、`skills/protocol.py`、`skills/loader.py` |
| 新增一个工具 | `drugpaper/tools/`，注册点在 `skills/bootstrap.py` |
| 图表理解/补救策略 | `agents/figure/graph.py`、`agents/figure/remediation.py`、`config/routing.yaml` |
| 分子检测/识别/Markush/拼接 | `skills/molecule.py`、`chem/`、`gateway/molparser.py`、`detection/` |
| 全文阅读/回查/综合 | `agents/reader/`、`agents/lookup/`、`orchestration/full_text_pipeline.py` |
| 交付 JSON 形状与校验 | `profiles/projector.py`、`validators/projection.py`、`validators/path.py` |
| 工作台单图执行 | `api/workbench.py`、`workbench/runner.py`、`workbench/snapshot.py` |
| 数据库表/迁移 | `persistence/models.py`、`persistence/migrations/versions/` |
| 模型路由/任务参数 | `config/models.yaml`、`gateway/task_router.py` |
| 批次与 Celery | `api/batches.py`、`workers/celery_app.py`、`workers/tasks.py` |
| 导出 ZIP | `exporters/package.py` |

---

<a id="sec-2"></a>
## 2. 快速开始与运行命令

### 2.1 安装与初始化

```bash
uv sync
cp .env.example .env          # 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL；DATABASE_URL 留空则用 SQLite
uv run drugpaper db upgrade   # PostgreSQL 使用 Alembic；SQLite 也会建表
```

### 2.2 CLI 命令（`drugpaper/cli.py`）

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `drugpaper ask` | 对 layout.json 或已入库文档提问 | `--doc`、`--max-steps`、`--max-model-calls`、`--max-tool-calls`、`--max-wall-time`、`--json` |
| `drugpaper resume` | 恢复一个已有 Agent run | run id |
| `drugpaper ingest` | PDF → MinerU → IR → 页面图 | `--offline`、`--force`、`--no-render` |
| `drugpaper db upgrade` | Alembic 迁移到 head | revision |
| `drugpaper molecules` | P4 分子检测/识别/标注/归一 | `--doc`、`--force` |
| `drugpaper serve` | 启动 FastAPI | `--host`、`--port`（默认 127.0.0.1:8000，无鉴权） |
| `drugpaper figures` | P5 图表 Agent 全量执行 | `--doc`、`--force` |
| `drugpaper text-facts` | P6 正文事实、名称、台账、测定、页包 | `--doc`、`--force` |
| `drugpaper understand` | P6 阅读 → 综合 → 路径校验 → 投影，可导出 ZIP | `--doc`、`--prepare-text/--no-prepare-text`、`--from-page`、`--export-dir` |

### 2.3 API 启动

```bash
uv run drugpaper serve --port 18000
# Swagger: http://127.0.0.1:18000/docs
# 健康检查: GET /healthz（返回 status/version，不在 /api/v1 下）
```

前端开发使用 `web/`，见前端 Codemap。离线联调：

```bash
cd web && npm run dev:offline
# 启动 scripts/dev_workbench.py（隔离 SQLite，数据在 data/workbench-dev/）+ Vite
```

### 2.4 开发验证

| 命令/脚本 | 说明 |
|---|---|
| `make test` / `pytest tests/unit` | 单元测试（102 个测试文件，分 unit/integration/smoke） |
| `make test-int` | PostgreSQL 集成测试（`TEST_DATABASE_URL` 或嵌入式 pgserver） |
| `make verify-p1..p6` | 各阶段离线验收 |
| `scripts/dev_workbench.py` | 独立前端联调后端；默认离线，不迁移业务库，不启动推理 |
| `scripts/verify_*` | 真实验收脚本（MinerU/YOLO/MolParser/OPSIN 等） |
| `scripts/evaluate_silver.py` | 与银标准比较（银标准不是人工金标） |

---

<a id="sec-3"></a>
## 3. 目录与模块总览

| 目录 | 文件数/行数（约） | 职责 | 关键文件 |
|---|---|---|---|
| `drugpaper/domain` | 6 / 475 | 纯领域模型：ID、几何、节点、Artifact、Work Unit | `ids.py`、`geometry.py`、`nodes.py`、`artifacts.py` |
| `drugpaper/persistence` | 18 / 1864 | SQLAlchemy 模型、会话、仓储、Alembic 迁移 | `models.py`、`db.py`、`repos/`、`migrations/` |
| `drugpaper/api` | 17 / 2404 | FastAPI 应用、依赖、错误、路由 | `app.py`、`deps.py`、`documents.py`、`workbench.py` |
| `drugpaper/services.py` | 1 | 服务容器 `Services` | `Services` dataclass |
| `drugpaper/config` | 3 / 379 | 环境配置 `Settings`、YAML 加载 | `settings.py` |
| `drugpaper/agent` | 9 / 1198 | P1 通用 Agent 运行时、QA Agent、校验、checkpoint | `runtime.py`、`qa.py` |
| `drugpaper/agents/figure` | 18 / ~4300 | 图表 Agent：状态图、探索、审查、补救、报告 | `graph.py`、`exploration.py`、`structure_review.py` |
| `drugpaper/agents/reader` | 7 | 逐页阅读与检查点 | `graph.py`、`page_pack.py`、`notes.py` |
| `drugpaper/agents/lookup` | 7 | 批量内容回查 | `batch.py`、`fast_path.py`、`vision.py` |
| `drugpaper/agents/coordinator` | 4 | 图表流水线协调器（黑板、分发、汇总） | `blackboard.py`、`dispatch.py`、`reconcile.py` |
| `drugpaper/skills` | 46 / 3693（不含 builtin 资源） | Skill 协议、加载、注册、运行时、内置能力实现 | `protocol.py`、`loader.py`、`registry.py`、`runtime.py`、`bootstrap.py` |
| `drugpaper/skills/builtin` | 44 个包 | 内置 Skill（prompt 型 + python 型） | 每包 `manifest.yaml` + `SKILL.md` + schemas/prompts/tests |
| `drugpaper/capabilities` | 7 / 795 | 能力索引、路由预选、渐进披露工具集、元工具、token 估算 | `assembler.py`、`router.py`、`toolset.py` |
| `drugpaper/tools` | 13 / 1196 | 工具协议、注册表、各类内置工具 | `protocol.py`、`registry.py`、`doc.py`、`chem.py` 等 |
| `drugpaper/orchestration` | 6 / 952 | 文档/图表/分子/正文/全文流水线 | `pipeline_graph.py`、`figure_pipeline.py`、`full_text_pipeline.py` |
| `drugpaper/parsers` | 19 / 2038 | MinerU 客户端、离线导入、适配、行增强、质量 | `mineru_legacy/` |
| `drugpaper/text` | 5 / 837 | 文本归一、grep、FTS/trgm/混合检索、词表 | `normalize.py`、`grep.py`、`index.py` |
| `drugpaper/render` | 3 / 138 | PDF 页面渲染、节点裁剪、图像缩放 | `pages.py`、`images.py` |
| `drugpaper/vision` | 7 / 453 | 框几何、图表 CV、SOM 编号图、overlay、文字层 | `boxes.py`、`som.py`、`chart_cv.py` |
| `drugpaper/storage` | 4 / 175 | 内容寻址 BlobStore 与资产登记 | `localfs.py`、`assets.py` |
| `drugpaper/detection` | 6 / 676 | 分子检测本地/远程后端、队列、Worker API | `service.py`、`backend.py`、`remote.py`、`api.py` |
| `drugpaper/gateway` | 7 / 1368 | 模型网关：OpenAI 兼容、任务路由、限流、外部服务客户端 | `openai_compat.py`、`task_router.py` |
| `drugpaper/chem` | 22 / 3333 | 化学：E-SMILES、Markush、R 基、拼接、差异、分子式/HRMS、OPSIN | `esmiles.py`、`splicer/`、`assembly.py`、`diff.py` |
| `drugpaper/facts` | 33 / 4106 | 事实模型、投影器、表格/文本抽取、测定、名称、路径、交付形状 | `projector.py`、`figure_projector.py`、`full_text.py`、`paths.py` |
| `drugpaper/validators` | 11 / 868 | 图表检查、路径校验、投影校验、工作流必需调用校验 | `figure/`、`path.py`、`projection.py` |
| `drugpaper/profiles` | 2 / 338 | legacy 交付投影与 audit 孪生 | `projector.py` |
| `drugpaper/workbench` | 5 / 769 | 工作台存储布局、快照、复用判定、隔离运行器 | `store.py`、`snapshot.py`、`runner.py` |
| `drugpaper/workers` | 3 / 165 | Celery 应用与任务 | `celery_app.py`、`tasks.py` |
| `drugpaper/exporters` | 5 / 408 | ZIP 导出包 | `package.py` |
| `drugpaper/tracing` | 4 / 323 | DB/JSONL/Composite/Null Tracer | `db.py`、`jsonl.py` |
| `drugpaper/evals` | 2 / 160 | 银标准比较工具 | `silver.py` |
| `prompts` | — | 公共提示词片段与 Reader 提示词 | `common/*.md`、`skills/reader_*` |
| `config` | — | 模型、路由、外部服务、指标字典 | `models.yaml`、`routing.yaml`、`services.yaml`、`metric_dictionary.yaml` |
| `services` | — | 独立外部服务代码（Agent/MolDet/OPSIN） | 各自 `server.py`、`Dockerfile` |
| `deploy` | — | YOLO GPU 安装、systemd、运维说明 | `README.md` |

---

<a id="sec-4"></a>
## 4. 分层架构与数据流

### 4.1 分层

```
HTTP / CLI / Celery
        │
        ├── api/*.py（参数校验、错误映射、分页、权限边界——当前仅 user_id=default）
        ├── orchestration/*.py（流程编排，只保存引用，不把大对象放进状态）
        ├── agents/*（FigureAgent / Reader / DocLookup / Coordinator）
        ├── skills/*（Skill 加载、注册、执行、缓存、提交）
        ├── capabilities/*（能力披露与路由）
        ├── tools/*（Agent 可调用工具，统一 ToolResult）
        ├── facts|validators|profiles（事实投影、校验、交付投影）
        ├── gateway|detection（模型网关、分子检测）
        ├── parsers|text|render|vision|storage（文档与图像基础能力）
        └── persistence/*（SQLAlchemy 仓储 + 迁移）
```

关键设计：

- **大对象不进流程状态**：LangGraph state 只放 `document_id`、`blob_ref`、`revision` 等引用；大 JSON 落 Artifact/Blob。
- **一切判断可追溯**：Run 记录（`runs` 表）+ Artifact 依赖 + EvidenceRef（节点、字符 span、区域）。
- **缓存以指纹为准**：同一 Work Unit 的 Artifact 复用必须 `input_fingerprint` 一致且状态非 failed。
- **失败暴露而非掩盖**：缺失依赖、证据不足、模型失败都会变成 `needs_review` / `pending` / `failed` + 错误信息，并保留中间产物。

### 4.2 一次上传/解析的时序

```
POST /api/v1/documents (multipart, mode=manual|auto)
  → 校验 PDF magic、大小 ≤200MB
  → BlobStore.put(PDF)
  → documents 行（按 user+sha256 去重）
  → 后台 DocumentPipeline.run：
       ingest → mineru_parse → build_ir → render_and_index
  → documents.status: uploaded → parsing → parsed → ir_built → ready（失败为 failed + error）
  → auto 模式：ready 后调用工作台 auto，为未完成/问题图表创建单图 run
```

### 4.3 一次图表理解的时序（P5）

```
FigurePipeline.run(document_id)
  → Coordinator.build_v1 生成 targets（scheme/table/figure，非 removed，有 region）
  → LangGraph Send 并行 figure_agent
      FigureAgent.run(node_ref, mode=full)
        classify → plan → prepare → structure → post
        → check_l1 → check_l2 →（必要时 remediate/verify）→ report
  → Coordinator.build_v2 + questions（跨图/文档级）
  → Send query 并行回答
  → 汇总 FigureReportRow（含 extraction、backfilled_extraction、checks、review、trace…）
```

### 4.4 一次全文理解的时序（P6）

```
FullTextPipeline.run(document_id, prepare_text=True, from_page=None)
  → TextPipeline（可选）：正文抽取、名称→结构、化合物归一、页包
  → ReaderAgent.read：逐页 page_pack → reader_page 模型 → NoteDelta → checkpoint
       open_questions 累积，按 8 个一批调用 DocLookup
  → synthesize：综合全文笔记，生成优化路径提案
  → build_steps + validate_path（V-P01..V-P08）
  → project：固定交付 JSON + audit 孪生，validate_projection（V-J/V-E）
  → 写 FullTextResultRow（绑定 revision + fingerprint）
  → 可导出 ZIP（exporters/package.py）
```

### 4.5 数据流总图

```
PDF ──Blob──> MinerU raw ──adapter──> IR（nodes/relations/meta/quality）──Postgres/SQLite
                                         │
                                         ├─ 页面渲染 ──> page_image 资产
                                         ├─ 文本归一/检索索引
                                         └─ FigureAgent / TextPipeline / Reader
                                                │
                                                ├─ Skill → Artifact（payload + 依赖 + 指纹）
                                                ├─ Facts 投影（结构/标签/测定/冲突…）
                                                └─ FigureReport / ReaderNote / FullTextResult
                                                        │
                                                        └─ Projection → 交付 JSON → ZIP
```

---

<a id="sec-5"></a>
## 5. 领域模型（`drugpaper/domain/`）

### 5.1 ID 与引用

| 对象 | 规则 | 文件 |
|---|---|---|
| Document | `doc_{ULID}`（26 位 Crockford base32） | `domain/ids.py::new_document_id` |
| 通用 ID | `{prefix}_{ULID}`（如 `alias_`、`fact_`） | `new_id` |
| 原始块 | `p{page:03d}.b{block_index:03d}` | `block_id` |
| 页内合并组 | `p{page:03d}.g{group_index:03d}` | `group_id` |
| 跨页合并组 | `x{first_page:03d}.g{group_index:03d}` | `cross_group_id` |
| 图注 | `{parent}.c{k}` | `caption_id` |
| 脚注 | `{parent}.f{k}` | `footnote_id` |
| 图内部件 | `{parent}.p{k}` | `part_id` |
| 小标题 | `{parent}.h0` | `run_in_heading_id` |
| 丢弃块 | `p{page:03d}.d{k:03d}` | `discarded_id` |
| 章节 | `s{k:03d}` | `section_id` |
| 分子子节点 | `{parent}.m{k:03d}`（由 molecule_detect 提案） | `skills/molecule.py` |
| 文档根/前置 | `doc` / `front` | `ROOT_ID` / `FRONT_ID` |
| 全局引用 | `{document_id}/r{revision}/{local_id}` | `global_ref` / `parse_ref` |

`GLOBAL_REF_RE` 为 `^(?P<doc>[A-Za-z0-9_\-]+)/r(?P<rev>\d+)/(?P<local>[A-Za-z0-9_.\-]+)$`。任何跨文档/跨 revision 引用在写入事实前都会被重新校验。

### 5.2 几何与坐标（`domain/geometry.py`）

- `BBox`：归一化 `x0,y0,x1,y1`，提供 `width/height/area/center/contains_point/in_bounds`。
- `Region`：`page`、`bbox`、`source_bbox_pt`、`role`（`primary|continuation|segment`）、`derived`。一个逻辑节点可以有多段区域（跨页、跨栏）。
- `Geometry`：`regions`、`local_bbox`、`parent_region_index`、`derived`。`local_bbox` 是相对父节点某一段区域的坐标。
- 换算工具：`pt_to_norm`、`pt_out_of_bounds`、`norm_to_pt`、`norm_to_px`、`local_to_page`、`page_to_local`、`intersection`、`iou`、`overlap_ratio`、`union_bbox`。
- 约定：API 返回的 bbox 一律是**页内归一化坐标**；前端按百分比叠加。分子框在工作台中用**父区域局部坐标**，保存时再换算。

### 5.3 节点与关系（`domain/nodes.py`）

`NodeType` 共 18 种：

| 分组 | 类型 |
|---|---|
| 结构 | `document`、`front_matter`、`section`、`heading`、`paragraph`、`list` |
| 视觉 | `figure`、`scheme`、`table`、`chart_panel`、`figure_part` |
| 附属 | `molecule`、`caption`、`footnote`、`formula`、`reference_entry` |
| 其他 | `discarded`、`unknown` |

- `VISUAL_TYPES = {figure, scheme, table, chart_panel}`；`TEXT_TYPES = {paragraph, heading, caption, footnote, list, formula}`。
- `RelationType`：`contains`、`caption_of`、`footnote_of`、`continuation`、`absorbed_from`。
- `DocumentNode` 关键字段：`document_id`、`revision`、`local_id`、`node_type`、`subtype`、`parent_id`、`children`、`reading_order`、`geometry`、`label`、`text_raw`、`text_norm`、`section_role`、`source`（`mineru|skill|human`）、`source_ref`、`quality_flags`、`status`（`active|removed|needs_review`）、`attributes`；`ref` / `page` / `flag` 为便捷属性。
- `EvidenceRef`：`node_id`、`field_path`、`char_span`（Unicode code point 半开区间）、`regions`、`artifact_id`、`role`（`primary|context|definition|condition|contradiction`）。
- `QualityIssue` / `QualityReport` / `DocumentMeta` / `PageInfo` / `IRDocument`（`pages/nodes/relations/meta/quality`，提供 `index/get/of_type`）。

### 5.4 Artifact、Work Unit 与依赖（`domain/artifacts.py`、`domain/work_units.py`）

- `work_unit_key(skill, target_ref, suffix=None)` → `{skill}:{target_ref}[:suffix]`。
- `split_work_unit_key` 反向解析。
- `node_dependency(node_ref, geometry_hash)` → `node:<ref>@<hash>`，用于把“节点几何”纳入 Artifact 依赖。
- Artifact 状态：`completed | validated | needs_review | stale | superseded | failed | human_corrected`。
- 依赖 kind：`input | context | validation`。
- `Artifact` 字段：`artifact_id`、`document_id`、`artifact_type`、`target_ref`、`work_unit_key`、`version`、`schema_name`、`schema_version`、`payload`、`blob_ref`、`input_fingerprint`、`produced_by_run_id`、`status`、`is_active`、`confidence`、`created_at`。

**版本规则**：`repos/artifacts.py::create_version` 在同一 `work_unit_key` 上把旧 active 置为 `superseded`，新版本号为 `max(version)+1`；PostgreSQL 下用 `SELECT ... FOR UPDATE` 防并发。缓存命中要求 active、指纹一致、状态非 failed。

### 5.5 证据与质量

- 证据必须指向当前 revision 中仍存在、非 removed 的节点；字符 span 必须在 `text_raw` 范围内（`facts/full_text.py::valid_evidence`）。
- 化学事实必须有 `role=primary` 的证据（V-E01）。
- `QualityReport` 汇总解析/适配阶段问题；`FigureReport.open_questions` / `checks` / `unchecked` 保留未解决项。
- 前端把 backend 的 code point span 转成 UTF-16 索引显示（见前端 Codemap）。

---

<a id="sec-6"></a>
## 6. 持久化层（`drugpaper/persistence/`）

### 6.1 连接与会话（`db.py`）

- `to_async_url`：自动把 `postgresql://` 转为 `postgresql+asyncpg://`，`sqlite://` 转为 `sqlite+aiosqlite://`。
- SQLite 每次连接执行 `PRAGMA foreign_keys=ON`。
- `Database.session(user_id="default")`：PostgreSQL 上先执行 `SELECT set_config('app.current_user_id', :uid, false)`，供 RLS/审计使用；正常结束 commit，异常 rollback。
- `create_all` 仅用于 SQLite/测试；PostgreSQL 必须 `alembic upgrade head`。
- `is_postgres` 用于选择部分索引、`tsvector`、递归 CTE、advisory lock 等 PG 特性。

### 6.2 表清单（27 张，`models.py`）

文档、运行、产物、Skill、能力、批次、回查、阅读、全文、导出等表带 `user_id`（默认 `'default'`）；化学事实表（如 `structure_candidates`、`measurements`、`assays`）通过 `document_id`（`ChemistryFactMixin`）归属，不直接带 `user_id`。JSON 字段在 PostgreSQL 用 JSONB、SQLite 用 JSON。

| 域 | 表 | 用途 |
|---|---|---|
| 文档 | `documents` | 文档主记录、sha256 去重、状态、active_revision、标题/DOI、quality、error、deleted_at |
| 文档 | `parse_revisions` | 每次 MinerU 解析的 revision、原始产物引用、parser/version |
| 文档 | `assets` | 内容寻址资产登记（pdf、page_image、裁剪图等） |
| 文档 | `nodes` | IR 节点（revision 维度），含 geometry、text、attributes、status、tsv（PG） |
| 文档 | `relations` | 节点关系（contains/caption_of/…） |
| 运行 | `runs` | 所有执行记录：run_type、component、trace_id、parent_run_id、status、metrics、error、时间 |
| 产物 | `artifacts` | Skill 产物版本、payload/blob、指纹、active、状态 |
| 产物 | `artifact_dependencies` | 依赖边（kind、dep key、节点几何 hash） |
| Skill | `skill_versions` | 包快照、content_hash、状态（discovered→validating→testing→staged/active/disabled/invalid）、scope |
| 能力 | `capability_index` | 能力卡索引（L0/L1、资源、受众、工具、token 估算） |
| 能力 | `capability_disclosures` | 每次披露/加载/使用记录、L2 token、上下文 |
| Skill | `skill_proposals` | Skill 运行产生的节点/事实/关系提案 |
| 化学 | `compounds` | 化合物台账（primary_label、accepted_structure_id、stereo_parent_id、payload） |
| 化学 | `compound_aliases` | 化合物别名 |
| 化学 | `structure_candidates` | 结构候选（E-SMILES、来源、node_ref、artifact、active、锁定字段） |
| 化学 | `label_claims` | 图内标签 → 结构候选的声明 |
| 化学 | `markush_scaffolds` | Markush 通式骨架 |
| 化学 | `rgroup_assignments` | R 基指派 |
| 化学 | `chemistry_conflicts` | 结构/标签冲突 |
| 化学 | `assays` | 实验条件（靶点、物种、体系、assay_type） |
| 化学 | `measurements` | 测定值（原值/单位/状态/证据/assay_id） |
| 化学 | `figure_relations` | 图表内反应/优化关系 |
| 图表 | `figure_reports` | 单图报告（mode、image_type、status、extraction/pre/post/backfill、checks、review、trace、run_id） |
| 批次 | `batches` | 批次状态、task_ids、document_ids、results |
| 回查 | `lookups` | DocLookup 结果缓存（问题、答案、证据、lookup_run_id） |
| 阅读 | `reader_notes` | Reader 检查点（page、version、payload、fingerprint、predecessor、active） |
| 全文 | `full_text_results` | 全文结果（revision、fingerprint、status、payload） |
| 导出 | `exports` | 导出包（blob_ref、文件清单、manifest、状态） |

### 6.3 迁移

`drugpaper/persistence/migrations/versions/`：

| 文件 | 内容 |
|---|---|
| `0001_core.py` | documents、parse_revisions、assets、nodes、relations、runs |
| `0002_text_index.py` | 文本索引（tsvector/trgm 相关） |
| `0003_skills.py` | skill_versions、artifacts、artifact_dependencies |
| `0004_capability_index.py` | capability_index、capability_disclosures |
| `0005_skill_proposals.py` | skill_proposals |
| `0006_chemistry.py` | compounds/aliases/structure_candidates/label_claims/markush/R 基/冲突/assays/measurements/figure_relations |
| `0007_figure_agents.py` | figure_reports |
| `0008_full_text.py` | batches、lookups、reader_notes、full_text_results、exports |

SQLite 测试通过 `create_all` 建表；迁移一致性由 `tests/unit/test_migrations_sqlite.py` 守护。

### 6.4 仓储行为（`repos/`）

| 仓储 | 关键行为 |
|---|---|
| `documents.py` | 按 `(user_id, sha256)` 去重返回 `(doc, created)`；`get_document` 排除 `deleted_at`；`set_status`；`next_revision`；`create_revision`；`get_revision` 取最新 |
| `nodes.py` | `save_ir` 单事务删除同 `(doc, rev)` 节点/关系后批量插入（500/批），更新文档标题/DOI/active_revision/meta/quality；PG 下 `update_tsv` 生成 `to_tsvector('simple', label \|\| text_norm)`；`load_ir`、`get_node`、`children`、`nodes_on_page`（首 region 或任一 region 在该页；默认排除 discarded）、`context_window`（同父兄弟前后 N）、`tree_from_nodes`、`get_tree` |
| `artifacts.py` | `create_version`（旧 active → superseded，PG 行锁，版本 +1）；`get_active`；`find_by_fingerprint`（active + 指纹一致 + 非 failed）；`versions`；`list_for_target`；`add_dependencies`；`upstream_of`；`downstream_of`（PG 递归 CTE） |

**并发控制**：Artifact 提交使用 PostgreSQL 事务级 advisory lock（按 document）；Reader checkpoint 对 Document 行 `FOR UPDATE` 并要求 predecessor 仍是 latest；Workbench 使用文件锁 + 数据库 slot。

---

<a id="sec-7"></a>
## 7. HTTP API（`drugpaper/api/`）

### 7.1 应用装配与生命周期

`api/app.py::create_app(services=None)`：

- 创建 `Services`（可从外部注入，便于测试）；
- SQLite 下 `create_all`；
- bootstrap Skill（注册/激活内置包、构建能力索引）；
- 启动 Skill watcher（`skills_watch_enabled`）与 Redis watcher；
- 挂载路由：documents、nodes、search、blobs、skills、compounds、figures、batches、full_text、monitoring、workbench；
- `/healthz` 返回 `{status, version}`；
- `export_openapi` 供脚本导出 OpenAPI。

统一前缀 `/api/v1`（`api/app.py` 中设置）；`/healthz`、`/docs`、`/openapi.json` 在根路径。

### 7.2 通用约定

- **错误格式**：`{"error": {"code": "...", "message": "...", "details": {...}}}`；`ApiError` 与 HTTPException/validation handler 统一包装（`api/errors.py`）。
- **鉴权**：当前无鉴权；所有读写使用 `user_id="default"`，不存在用户隔离。服务只应绑定 127.0.0.1 或可信内网。
- **分页**：列表接口普遍使用 `limit/offset`，上限由各路由定义。
- **大对象**：结果 JSON 通过 Artifact/Blob 提供，避免重复写库。
- **缓存头**：Blob 与图/裁剪图带 immutable cache；动态结果 JSON 带 `Cache-Control: no-store`。

### 7.3 路由总表

#### 文档（`api/documents.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents` | multipart 上传 PDF；≤200MB；校验 `%PDF` magic；`mode=manual\|auto` 记录工作台模式，`understand=true` 改为 Celery 队列；返回 202 + `document_id`/`status`/`duplicate`；后台跑 DocumentPipeline；auto 在 ready 后触发工作台 auto；同 user+sha 重复上传会复用已有文档 |
| GET | `/api/v1/documents` | 文档列表（默认 limit/offset） |
| GET | `/api/v1/documents/{document_id}` | 文档详情（状态、active_revision、quality、error） |
| GET | `/api/v1/documents/{document_id}/tree` | 轻量文档树（类型/标签/页码/章节） |
| GET | `/api/v1/documents/{document_id}/pages/{page}/image` | 页面 PNG；`dpi` 36–300；缓存 |
| GET | `/api/v1/documents/{document_id}/pages/{page}/nodes` | 页内节点区域；`include_discarded` 控制；排除结构分子节点 |

#### 节点（`api/nodes.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/nodes/{node_ref:path}/crop` | 节点裁剪图；`dpi`、`pad`；多区域默认取主区域 |
| GET | `/api/v1/nodes/{node_ref:path}` | 节点详情；OCR 行由原始 `raw_middle_json` 的 `aligned_block` 重建，失败给 `ocr_render_warning` |

> 路由顺序注意：`/nodes/{node_ref:path}/crop` 必须注册在 `/nodes/{node_ref:path}` 之前。

#### 检索（`api/search.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/documents/{document_id}/search` | `q`、`mode=grep\|fts\|trgm\|hybrid`、`literal`、`limit`；返回命中节点、snippet、char_span、score |

#### Blob（`api/blobs.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/blobs/{ref}` | 校验 64 位 hex sha256；嗅探 content-type；`Cache-Control: immutable` |

#### 化合物（`api/compounds.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/documents/{document_id}/compounds` | 化合物列表（分页） |
| GET | `/api/v1/documents/{document_id}/compounds/{compound_id}` | 详情：payload、accepted structure、全部候选、冲突 |

#### 图表报告（`api/figures.py`、`api/figure_results.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/documents/{document_id}/figure-reports` | 报告列表（最新在前）；惰性补写 result file 元数据 |
| GET | `/api/v1/documents/{document_id}/figure-reports/{report_id}/result` | 最终结果 JSON；报告不存在 404；无回填结果 409；禁止 NaN |
| GET | `/api/v1/documents/{document_id}/measurements` | 测定列表 |
| GET | `/api/v1/documents/{document_id}/assays` | 实验条件列表 |

#### 全文（`api/full_text.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents/{document_id}/lookups` | 1–8 个问题；返回批量回查结果（带证据） |
| POST | `/api/v1/documents/{document_id}/reader` | 顺序阅读；可指定 `from_page` |
| POST | `/api/v1/documents/{document_id}/reader/reread` | 指定页重读，必须给 `from_page` |
| POST | `/api/v1/documents/{document_id}/full-text` | 执行全文理解；`prepare_text`、`force`、`from_page` |
| GET | `/api/v1/documents/{document_id}/full-text` | 读取当前 revision + fingerprint 匹配的结果，否则 404 |
| POST | `/api/v1/documents/{document_id}/exports` | 生成 ZIP；校验当前 revision/fingerprint 与投影 V-J/V-E 阻断项 |
| GET | `/api/v1/exports/{export_id}/download` | 下载 ZIP |

#### 批次（`api/batches.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/batches` | 仅 ready 文档；去重；≤128；`queue=batch\|interactive`；提交 Celery `process_document`；返回 202 |
| GET | `/api/v1/batches/{batch_id}` | 批次状态、task_ids、document_ids、results |

#### Skill（`api/skills.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/skills` | 版本目录（当前可见 Skill） |
| GET | `/api/v1/skills/catalog` | 能力目录；`audience=chat`、`toolkit`、`q` |
| GET | `/api/v1/skills/{name}/versions` | 已注册版本与状态 |
| GET | `/api/v1/skills/{name}/card` | 能力卡（L0/L1/资源索引） |
| GET | `/api/v1/skills/{name}/resources` | L3 资源（按 section 读取） |
| POST | `/api/v1/skills:reload` | 重新扫描并注册包；需 `X-Admin-Token` 等于 `skills_admin_token` |

#### 监控（`api/monitoring.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/documents/{document_id}/runs` | Run 列表；`status`、`component`、`trace_id`、`limit/offset`；含全部 revision |
| GET | `/api/v1/documents/{document_id}/runs/{run_id}` | Run 详情；凭据与长字段脱敏 |
| GET | `/api/v1/documents/{document_id}/reader-notes` | 当前 revision 的 active 阅读检查点（分页） |

#### 工作台（`api/workbench.py`，仅开发用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/documents/{document_id}/workbench` | 总览：revision、stages、figures、counts、image_types、mode |
| GET | `/api/v1/documents/{document_id}/workbench/layout` | 指定页的布局节点（含文本、区域） |
| GET | `/api/v1/documents/{document_id}/workbench/runs` | 单图或整文档 run 列表；`node_ref` |
| POST | `/api/v1/documents/{document_id}/workbench/runs` | 创建单图 run（`node_ref`、`start`、`stop_after`、`pause_before`、`skill`、`source_run`、复用/操作字段）；返回 202 |
| GET | `/api/v1/documents/{document_id}/workbench/runs/{run_id}` | run 详情：状态、steps、states、events、process_error |
| GET | `/api/v1/documents/{document_id}/workbench/runs/{run_id}/result` | 最终结果文件（final-result / delivery-result） |
| GET | `/api/v1/documents/{document_id}/workbench/runs/{run_id}/file` | 该 run 快照中某文件内容 |
| POST | `/api/v1/documents/{document_id}/workbench/runs/{run_id}/pause` | 请求在当前阶段后暂停（写 control.json） |
| GET | `/api/v1/documents/{document_id}/workbench/files` | 可编辑文件列表；`node_ref`；带 changed/local/version |
| PUT | `/api/v1/documents/{document_id}/workbench/files` | 保存局部文件（带 expected_version） |
| POST | `/api/v1/documents/{document_id}/workbench/validate` | 校验已保存包（load_package） |
| POST | `/api/v1/documents/{document_id}/workbench/publish` | 把局部修改发布为全局版本 |
| GET | `/api/v1/documents/{document_id}/workbench/molecules` | 当前图的分子列表与人工修改版本 |
| POST | `/api/v1/documents/{document_id}/workbench/corrections` | 人工校正：type / restore / box / smiles |
| POST | `/api/v1/documents/{document_id}/workbench/auto` | 为未完成/问题图表批量创建 run；返回 202 |

### 7.4 工作台 API 内部机制

- `scope()`：校验文档归属（当前仅 default）、文档存在、IR 存在、revision 匹配、目标节点类型允许；所有端点先过 scope。
- `mutation()`：用 `fcntl` 文件锁串行化同一工作区的文件修改。
- `required_stage(path)`：把“被修改的文件/Skill”映射为最早受影响阶段，用于决定从哪一步重跑：
  `classify / plan / detect / recognize / som / structure / post / check_l1 / check_l2 / report`。
- `launch()`：快照工作区（skills/models/routing 覆盖）→ 构建 initial_state → 写 `run.json`/`settings.json` → 以隔离子进程 `python -m drugpaper.workbench.runner <run_dir>` 启动；移除代理环境变量，避免误连外部网络。
- `corrections`：
  - `type`：人工指定 image_type，下次运行重新路由；
  - `restore`：恢复机器原始结果；
  - `box`：新增/移动/删除分子框（局部坐标），可 `deleted: true`；
  - `smiles`：经 `chem.esmiles.split` + `canonical` 校验后，创建 manual StructureCandidate 与新的 Artifact 版本，递增 correction_version。

---

<a id="sec-8"></a>
## 8. 服务装配与配置

### 8.1 `Services`（`drugpaper/services.py`）

`Services` 是运行时容器，字段包括：

| 字段 | 类型/说明 |
|---|---|
| `settings` | `Settings` |
| `db` | `Database` |
| `blobs` | `LocalFsBlobStore` |
| `renderer` | `PageRenderer` |
| `mineru` | 惰性 `MinerUClient` |
| `molparser` | 惰性 `MolParserClient` |
| `opsin` | 惰性 `OpsinClient` |
| `_skills` | 惰性 `SkillServices`（注册表、运行时、工具、索引、模型、服务引用、watcher），异步锁保护 |
| `detection` | 惰性检测服务：`yolo_backend=remote` → `RemoteDetectionService`；否则 `MoleculeDetectionService` + `YoloBackend` |

方法：`store_for(user_id)`、`renderer_for`、`await skills()`、`await aclose()`。

### 8.2 `Settings`（`config/settings.py`，环境变量）

| 分组 | 关键项 |
|---|---|
| 基础 | `DRUGPAPER_ENV`、`DATABASE_URL`（空 → SQLite）、`REDIS_URL`、`RUNS_DIR`、`BLOB_ROOT`、`TRACE_JSONL` |
| 配置路径 | `MODELS_CONFIG_PATH`、`ROUTING_CONFIG_PATH`、`SKILLS_ROOT`、`SKILLS_WATCH_ENABLED`、`SKILLS_ADMIN_TOKEN` |
| MinerU | `MINERU_URL`、`MINERU_MAX_INFLIGHT`、`MINERU_TIMEOUT_S`、`MINERU_POLL_INTERVAL_S`、`MINERU_SUBMIT_ATTEMPTS`、`MINERU_BACKOFF_S`、各阶段超时 |
| MolParser | `MOLPARSER_BASE_URL`、`MOLPARSER_TIMEOUT_S`、`MOLPARSER_REC_ONLY`、`MOLPARSER_MODEL_VERSION` |
| OPSIN | `OPSIN_BASE_URL`、`OPSIN_TIMEOUT_S`、`OPSIN_MODEL_VERSION` |
| YOLO 检测 | `YOLO_BACKEND=local\|remote`、`YOLO_MODEL_PATH`、`YOLO_DEVICE`、`YOLO_BASE_URL`、`YOLO_SERVICE_TOKEN`、`YOLO_CONF`、`YOLO_HIGH_RECALL_CONF`、`YOLO_IOU`、`YOLO_IMGSZ`、`YOLO_PADDING_RATIO`、`YOLO_MAX_DETECTIONS`、队列/超时/缓存/像素/字节上限 |
| 图表探索 | `FIGURE_EXPLORATION_ENABLED`、`MAX_TURNS`、`MAX_TOOL_CALLS`、`TIMEOUT_S`、`MAX_VISUAL_READS`、`PREVIEW_BATCH_SIZE`、`MAX_PREVIEW_BATCHES`、`MAX_CONTEXT_IMAGES`、`MAX_REQUEST_BYTES`、`MAX_EXTRACTIONS`、`MAX_REGENERATIONS` |
| 并发/渲染 | `FIGURE_CONCURRENCY`、`MODEL_ENDPOINT_CONCURRENCY`、`RENDER_DPI`、`CROP_DPI` |
| Celery | `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`（空则从 Redis 派生） |

`get_settings()` 有 `lru_cache`；`Settings` 使用 `pydantic-settings`，空字符串路径会回退默认值。

### 8.3 YAML 配置

#### `config/models.yaml`

- `providers`：`default_llm`、`structure_llm`（240s）、`default_vlm`。
- `models`：`text_llm`、`text_llm_fallback`、`structure_gpt`、`vlm_large`、`vlm_small`。
- `pools`：`llm`、`vlm_light`、`vlm_strong`。
- `stages`：`default/classify/describe/structure/chat`，定义温度、max_tokens、超时、思考模式等。
- `tasks`（任务级参数覆盖）：

| task | 主要参数 |
|---|---|
| `visual_classify` | 768px，30s |
| `paper_qualify` / `figure_describe` / `chat` | 见配置 |
| `figure_structure` | 1600px，240s；超时重试 ×2，最大 600s |
| `figure_structure_review` | 结构化审查 |
| `figure_explore` | 2400px，探索 |
| `figure_self_check` | 自检 |
| `figure_remediate_vision` | 2000px，定向重读 |
| `remediate_decide` | 补救动作决策 |
| `metric_map_fallback` | 指标映射回退 |
| `figure_query` / `visual_reclassify` | 查询/重分类 |
| `text_extract` / `lookup_plan` / `lookup_answer` / `vision_lookup` | 正文与回查 |
| `reader_page` / `reader_synthesize` | 逐页阅读 / 全文综合（6000 tokens，180s） |

#### `config/routing.yaml`

按图像 subtype 给出 Skill 链，FigureAgent 的 `plan` 阶段直接消费：

| subtype | 链 |
|---|---|
| `synthetic_scheme_single` / `synthetic_scheme_multi` | `molecule_detect → molecule_recognize → som_render → scheme_extract → mol_backfill` |
| `sar_table` | `molecule_detect → molecule_recognize → som_render → table_extract → measurement_map → sar_record → mol_backfill` |
| `data_table` | `molecule_detect → molecule_recognize → som_render → table_extract → measurement_map → mol_backfill` |
| `reported_structures` | `molecule_detect → molecule_recognize → som_render → label_bind → structures_extract → mol_backfill` |
| `molecular_docking` / `protac_architecture` / `metabolic_pathway` / `pharmacophore_model` | `molecule_detect → molecule_recognize → som_render → {subtype}_extract → mol_backfill` |
| `property_plot` / `data_chart` | `molecule_detect → molecule_recognize → som_render → chart_digitize → mol_backfill` |
| `multi_panel` | `molecule_detect → molecule_recognize → som_render → panel_split → common_extract → mol_backfill` |
| `other` | `molecule_detect → molecule_recognize → som_render → common_extract → mol_backfill` |

#### `config/services.yaml`

- `moldet`：`YOLO_BASE_URL`、`DEVICE`、token、`worker_processes: 1`。
- `molparser`：`base_url`、`model_version`、`max_batch_size: 32`、`input_index` 对齐。

#### `config/metric_dictionary.yaml`

指标字典：`ec50/ic50/kd/…`，含 `synonyms`、默认单位、hard/soft range、`unit_class`。用于测定值的单位归一、指标映射与审计。

### 8.4 模型网关与任务路由（`gateway/`）

- `task_router.py::TaskRouter`：解析 `task → stage → pool → model → provider`，缓存 `ResolvedModel`；`candidates()` 支持主备与重试耗尽后回退；`_limit_images` 按 `image_max_side` 缩放；`apply_task_params` 只填充未显式设置的 `temperature/max_tokens/timeout_s/thinking`。
- `RoutedModelClient.chat`：按候选逐个尝试；只有 retryable 错误（超时/限流/5xx/网络）才回退；可选 `EndpointLimiter`（Redis 租约信号量）。
- `openai_compat.py`：同时支持 Responses 与 Chat Completions 两种 API；消息/工具/结构化输出转换；structured_output 失败时回退；错误映射到 `ModelError` 子类。
- `base.py`：`TextPart/ImagePart/ToolCall/Message/ToolSchema/Usage/ChatRequest/ChatResponse`；`ModelError`、`ModelTimeout`、`RateLimited`、`ModelBadResponse`、`SchemaViolation`、`ModelUnavailable`。

---

<a id="sec-9"></a>
## 9. 文档解析与 IR

### 9.1 MinerU 接入（`parsers/mineru_legacy/`）

| 模块 | 职责 |
|---|---|
| `client.py` | 提交 PDF、轮询状态、下载结果；队列满 429 有重试；超时/重试策略可配 |
| `importer.py` | 读取在线或离线 `auto/` 输出；`read_output_dir`、`store_result`、`resolve_output_dir` |
| `adapter.py` | MinerU 原始单元 → 节点类型 → 成员合并 → 跨页续接 → 章节 → 前置信息 → 阅读顺序 → 质量报告；入口 `builder.build_ir` |
| `schemas.py` | 宽松 Pydantic 模型，保留未知字段 |
| `line_enricher.py` | 用 `middle.json` 对齐 OCR 行，产出 `aligned_block`，供节点详情重建 OCR 行 |
| `quality.py` | 解析质量检查 |
| `snapshot.py` / `paths.py` | 产物快照与路径解析 |

### 9.2 构建 IR

`builder.build_ir` 的核心步骤：

1. 原始块 → `block_id`；
2. 页内合并 → `group_id`（可带图注、脚注、部件）；
3. 跨页合并 → `cross_group_id` 与 `continuation` 关系；
4. 识别章节与标题，生成 `section_id`、`heading`、`section_role`；
5. 前置信息 `front`（标题、作者、摘要等）；
6. 计算阅读顺序（按页、块序、视觉优先级）；
7. 产出 `PageInfo`、`DocumentMeta`、`QualityReport`；
8. 给视觉节点补 `image_blob`（通过 BlobStore）。

### 9.3 页面渲染与裁剪（`render/`）

- `render_page`：缓存键 `pdf_sha/page/dpi`；输出 PNG。
- `crop`：缓存键 `pdf_sha/page/bbox/dpi/pad`。
- `PageRenderer`：统一入口；`render/images.py` 负责最长边缩放（模型输入用）。
- API 页面图 DPI 限制 36–300；裁剪默认 `crop_dpi=300`，前端常用 144。

### 9.4 文本归一与检索（`text/`）

- `normalize.py`：字符替换规则链，每个字符保留 raw `[start,end)` 映射；`normalize_detail/normalize/normalize_text/map_span/compact_key`。
- `grep.py`：在 `text_norm` 上正则匹配（带超时保护），映射回 `text_raw` 的 char_span；`GrepEngine` 按 `(doc, rev)` 缓存。
- `index.py`：`fts`（PG `to_tsvector` + `websearch_to_tsquery`）、`trgm`（`pg_trgm` + `word_similarity`，不可用回退 Python）、`hybrid`；`InMemorySearch` 用 TF-IDF/三元组；`PostgresSearch` 用 tsv/trgm。
- `vocab.py`：`wordninja` 词表，处理行尾连字符与粘连词拆分。

### 9.5 文档源抽象（`docsrc/`）

- `DocumentSource` 协议：`outline/node/children/window/section/grep/page_nodes/sections/descendants`。
- `ir_source.py`：基于 IR/数据库的实现；`layout_json.py`：P1 的 `layout.json` 实现。
- 这层让 Agent/工具不直接依赖存储实现。

---

<a id="sec-10"></a>
## 10. Agent Runtime 与工具系统

### 10.1 运行时（`agent/runtime.py`）

LangGraph 节点：

```
START → assemble → call_model
                     ├─ tools → execute_tools → call_model / finalize / answer
                     ├─ answer（submit_answer）→ validate
                     ├─ finalize（预算耗尽等）→ validate
                     └─ error → finalize
validate ├─ done
         └─ retry（把失败原因作为工具消息反馈）
```

- `run(user_input, ctx)` / `resume(ctx)`：`thread_id=run_id`；`recursion_limit = max_steps*3*(retries+1)+20`。
- 预算：`max_steps`、`max_model_calls`、`max_tool_calls`、`max_wall_time_s`；超限调用返回 `budget exhausted`；耗尽进入 force_finalize，并附 `BUDGET_WARNING`。
- 答案获取：优先 `submit_answer` 工具（answer_model schema）；模型只回纯文本时，额外发 1 次结构化转换请求（计入预算）。
- 校验：JSON Schema + validators；不可重试时 `AnswerSanitizer` 清理，`stop_reason` 为 `STOP_ANSWERED | STOP_BUDGET_EXHAUSTED | STOP_VALIDATION_FAILED | STOP_ERROR`。
- 事件：`model_call`（usage、latency）、`tool_call`（参数/结果摘要、latency、refs、artifact_ids、needs_review）、`validation`、`budget_exhausted`、`cancelled`；超时/取消写 tracer。

### 10.2 工具协议（`tools/`）

`ToolSpec`：

| 字段 | 说明 |
|---|---|
| `name` / `description` | 工具名与描述 |
| `input_model` / `parameters_schema` | Pydantic 输入模型与压缩 JSON Schema |
| `output_model` | 输出模型（可选） |
| `side_effect` | `read / compute / write_fact / external_model` |
| `timeout_s` | 单次超时 |
| `max_output_chars` | 默认 4000；超长截断并只保留截断文本中可见的 refs |

- `@tool` 装饰器要求 async，且第二参数为 Pydantic 模型。
- `ToolRegistry.invoke` **永不抛业务异常**：未加载/未知工具、参数 JSON 错误、Pydantic 校验失败、超时、内部异常都转 `ToolResult(ok=False)`；Skill 可信代码可 `truncate=False`。
- `ToolContext` 携带 user_id、document_id、revision、run_id、tracer 等。

### 10.3 内置工具（注册于 `skills/bootstrap.py`）

| 组 | 工具（示例） |
|---|---|
| 化学 | `opsin.name_to_structure`、`molparser.recognize`、分子规范化/拆分 |
| 检测 | `detect_molecules`（本地服务或远程 Worker） |
| 化合物 | `get_compound`、`query_measurements`、`get_figure_report`、`get_table` |
| 文档 | `doc_read`、`search`、`grep`、`get_node`、`get_context_window`、`get_section`、`get_outline`、`get_children`、`get_page_text`、`preview_pages`、`inspect_*` |
| 图表事实 | `figure_*` 查询与拼接能力 |
| Skill 工具 | `SkillTool` 把 Skill 暴露为工具（见 11.5） |

工具的 `refs` 会进入 Agent 事件，供前端做证据跳转。

---

<a id="sec-11"></a>
## 11. Skill 体系

### 11.1 Skill 包结构

```
drugpaper/skills/builtin/<name>/
├── manifest.yaml        # 契约：kind/entry/schemas/accepts/produces/tools/model_tasks/budgets/disclosure…
├── SKILL.md             # frontmatter(description=L0) + 固定小节(L1)
├── schemas/
│   ├── input.json
│   └── output.json
├── prompts/             # prompt 型 Skill 的 Jinja2 模板（StrictUndefined）
├── scripts/main.py      # python 型 Skill 的入口（通常很薄）
├── references/          # L3 资源（*.md）
└── tests/cases/*.json   # 回放测试用例（+ *.response.json）
```

### 11.2 Manifest 与协议（`skills/protocol.py`）

`SkillManifest` 关键字段：

- 身份：`name`、`version`、`scope`（`builtin|system|local`）、`kind`（`prompt|python`）。
- 执行：`entry`、`prepare`、`postprocess`、`input_schema`、`output_schema`。
- 能力声明：`accepts`（node_types/subtype）、`produces`（artifact_type/fact types/node types）、`tools`（允许调用的工具白名单）、`model_tasks`（允许使用的模型任务）。
- 预算：`budgets`（max_model_calls、max_tokens 等）。
- 其他：`prompts`/`includes`、`sandbox`、`cache`、`triggers`、`disclosure`（能力卡）、`vision_structuring`、`enabled`、`auto_activate`、`override`。

`SkillResult`：`ArtifactDraft`、`NodeProposal`、`FactProposal`、`RelationDraft`、`SkillQuality`。

`ToolCaller` 受 manifest 白名单约束；`SkillBudget` / `SkillContext` 传递预算与上下文。

### 11.3 加载与校验（`skills/loader.py`）

`load_package` 校验顺序与错误码：

| 错误码 | 含义 |
|---|---|
| `E_NOT_A_DIRECTORY` / `E_MANIFEST_MISSING` / `E_MANIFEST_YAML` / `E_MANIFEST_SCHEMA` | 目录/清单问题 |
| `E_NAME_MISMATCH` / `E_SCOPE_MISMATCH` | 名称或 scope 与目录/请求不一致 |
| `E_SKILL_MD_MISSING` / `E_FRONTMATTER` | SKILL.md 与 frontmatter |
| `E_L0_BUDGET` / `E_L1_BUDGET` | L0/L1 token 超预算 |
| `E_SCHEMA_FILE` / `E_SCHEMA_INVALID` | input/output schema 缺失或非法 |
| `E_PROMPT_REQUIRED` / `E_PROMPT_MISSING` / `E_PROMPT_COMPILE` / `E_INCLUDE_MISSING` | prompt 型 Skill 的模板与公共片段 |
| `E_ENTRY_MISSING` / `E_KIND_UNSUPPORTED` | python 入口/kind 不支持（graph 暂不支持） |
| `E_TASK_REQUIRED` / `E_TASK_UNKNOWN` / `E_TOOL_UNKNOWN` | model_tasks/tools 合法性 |
| `W_L1_SECTION_MISSING` / `W_NO_TESTS` | 警告 |

`LoadedSkill` 提供：`manifest`、`l0`、`l1_sections`、`resources_index`、`prompts`、`fragments`、`input_schema`、`output_schema`、`package_hash`、`content_hash`、`prompt_hash`、`errors`、`warnings`、`ok`、`name`、`version`、`skill_id=name@version#hash[:12]`、`l1`、`l0_tokens`、`l1_tokens`、`render`、`system_text`、`read_resource(section, max_tokens=1500)`。

SKILL.md 固定小节：何时使用 / 何时不用 / 输入 / 输出 / 代价与限制（前四必需）；`##`/`###` 切分。

### 11.4 注册、激活与解析（`skills/registry.py`）

- `register(path, scope)`：
  1. `load_package`；
  2. 内容寻址快照到 `snapshot_root/{content_hash}/{name}`；
  3. 公共片段写到 `_common`；
  4. 从快照重新加载并校验 hash；
  5. 写/复用 `SkillVersionRow`，状态 `discovered → validating → testing → staged | invalid`；
  6. `test_package` 用 `tests/cases/*.json` + `response_file` 回放，校验 expected payload/status；
  7. `auto_activate`/`enabled` 时激活。
- `activate`：只允许 `staged|active|disabled`；PostgreSQL advisory lock；同 scope 旧 active 置 `staged`；system 覆盖 builtin 需 `override: true`。
- `resolve(name)`：`local_packages` 优先；DB active 的 builtin/system，system 优先；按需从快照加载。
- `list_visible(user_id, audience)`；`scan(roots)` 注册并禁用已删除包。
- `ReplayModel`：用固定 payload 回答，用于注册测试与无网络回放。

### 11.5 运行时（`skills/runtime.py`）

`SkillRuntime.run(skill_name, target_ref, inputs, ctx, force=False)`：

1. 预算取 `min(manifest, ctx.extras)`；生成 `work_unit_key`；
2. 同进程 `asyncio.Lock` 防止同 key 并发；
3. 计算 `input_fingerprint`（见下）；
4. 缓存查询 `find_by_fingerprint`；命中返回 `SkillOutcome(cached=True)` 并写 RunRow；
5. 校验输入 Schema 与 `accepts.node_types`；
6. 执行（prompt 型通过模型，python 型 `invoke_entry`）；
7. 按 quality/payload 改写 artifact.status（completed / needs_review / failed）；
8. `lock_check`、manifest.validators；
9. `_commit` 事务提交。

`input_fingerprint` 组成（`execution_contract=skill-delivery-v2`）：framework_version、inputs、target、skill.content_hash、prompt_hash、模型配置、user_id、tool_fingerprints、debug_code、upstreams、node_hash、target_material、metric_dictionary（测量类）、reference_images、images。`text_extract` / `name_to_structure` / 识别 / SOM / 视觉分类有专门裁剪规则，避免无关字段导致缓存抖动。

`validate_result`：

- 必须恰好 1 个 Artifact；
- 质量 `failed` 不得带提案；
- 输出 JSON Schema；
- `artifact_type` / `target_ref` 不得越权；
- fact type / node type / 父节点 / 几何 / 关系端点白名单校验。

`_commit`：

- 3 次重试；PostgreSQL 事务级 advisory lock（document）；
- `create_version` 写 Artifact；
- 写依赖（`materials.upstream_versions`、`node_dependency`）；
- 调用 `facts.projector.project` 与 `facts.figure_projector.project`（同事务，失败整份回滚）；
- 正文类额外校验 revision/text_raw/section_role 未变；
- 写 `SkillProposalRow`（node/fact/relation）；
- 写 `RunRow(status=completed, metrics.model_calls)`；
- 提交成功后的 `event_sink` 失败只记日志，不回滚。

失败路径：写 `RunRow(status=failed, error, metrics)`，返回 `SkillOutcome("failed")`。

### 11.6 执行路径（`skills/execution.py`）

- `PackageOutput`：暴露包的 JSON Schema（含本地 `$defs`）。
- `invoke_entry`：加载 `scripts/main.py` 并调用入口函数。
- `execute(skill, inputs, ctx)`：无持久化执行路径，供注册测试与 Runtime 共用。

### 11.7 渐进披露与能力路由（`capabilities/`）

- `assembler.py`：固定顺序 = 系统规则 → 任务材料 → 能力描述（≤ `capability_budget`）→ 历史 → 输出预留。`pack_capabilities` 按分数排序，超预算先低分 L1→L0，仍超则删除；`PromptAssembler.build` 任务材料超预算抛 `PromptBudgetError`（不静默截断）；`prompt_hash` = 系统文本 + 能力块 + 任务材料。
- `router.py`：确定性预选。分数 = `100×failed_check 命中 + 50×image_type 命中 + 10×intent 命中`；有 `failed_checks` 时必须匹配失败码；取 `max_candidates`，前 3 个给 L1；最后按预算打包。
- `toolset.py::DisclosureToolset`：可用工具 = core + 元工具（`find_capability` / `read card` / `activate` / `unload` / `read resource`）+ 已加载能力的工具；`prepare` 做 LRU/4 轮空闲卸载；`record` 写 `CapabilityDisclosureRow`（L2 token、used、context）。
- `toolset.py::SkillTool`：把 Skill 暴露为工具。参数 envelope：`target_ref`、`inputs`、`artifact_inputs`；从持久化 Artifact 读取大 JSON（校验 user/doc/target/active/status）；`mol_backfill` 的 `extraction_payload` 必须是视觉结构化产物；`vision_structuring` 时先 `prepare_vision`；结果过大只返回 artifact 元数据与 `payload_omitted`；`refs=[target_ref]`。
- `index.py` / `meta_tools.py` / `tokens.py`：能力索引刷新/可见性；元工具；token 估算（CJK 1 token/字，其余约 4 字符/token，可注入真实 tokenizer）。

### 11.8 内置 Skill 清单（44 个）

#### 图表核心链

| Skill | kind | 主要产出/作用 |
|---|---|---|
| `visual_classify` | prompt | 图像类型与 subtype 分类（confidence） |
| `visual_reclassify` | prompt | 低置信度二次分类 |
| `molecule_detect` | python | YOLO 检测分子框，产出 `molecule_boxes` 与子节点提案 |
| `molecule_recognize` | python | 调用 MolParser 识别每个分子（可单分子） |
| `som_render` | python | 生成编号图（SOM），供视觉模型引用分子编号 |
| `label_bind` | python | 图内标签 → 结构候选绑定 |
| `mol_backfill` | python | 把结构回填到表格/图表/流程产物 |
| `table_extract` | python | 表格网格/单元格结构化 |
| `measurement_map` | python | 表格/图表数值 → 测定事实 |
| `sar_record` | python | SAR 表记录（化合物-指标-值-条件） |
| `chart_digitize` | prompt | 曲线/柱状图数字化 |
| `chart_measurements` | python | 图表点 → 测定事实 |
| `scheme_extract` | prompt | 合成路线结构化 |
| `scheme_relations` | python | 反应关系（反应物/产物/条件）持久化 |
| `structures_extract` | prompt | 报道结构（reported structures） |
| `common_extract` | prompt | 通用图表抽取 |
| `panel_split` | python | 多面板拆分并递归生成子图 Agent |
| `figure_self_check` | prompt | 原图 + overlay/crop 自检 |
| `structure_audit` | prompt | 结构审计（交付前） |
| `figure_describe` | prompt | 旧版图描述（`enabled=false`） |
| 其他 subtype | prompt | `molecular_docking_extract`、`protac_architecture_extract`、`metabolic_pathway_extract`、`pharmacophore_model_extract` |

#### 分子/台账

| Skill | kind | 作用 |
|---|---|---|
| `compound_resolve` | python | 文档级化合物归一（编号/别名/候选/冲突） |
| `markush_assemble` | python | Markush 通式与 R 基拼接 |
| `name_to_structure` | python | 正文实验名称 → OPSIN → 结构候选 |

#### 正文

| Skill | kind | 作用 |
|---|---|---|
| `text_extract` | python | 段落/列表正文事实（优化叙事、SAR、测定提及、拒绝无依据字段） |
| `paper_qualify` | prompt | 论文是否为目标类型（可选前置筛） |

#### 工作台补救动作（python）

`action_get_figure_report`、`action_grep`、`action_search`、`apply_review_fix`、`apply_text_layer`、`fix_unit`、`give_up`、`rebind_labels`、`recalibrate_axes`、`reclassify`、`recognize_region`、`redetect`、`reslice_table`、`retranscribe_cells`、`split_panels`。

每个包都有 `manifest.yaml`、`SKILL.md`、schemas、prompts、tests/cases；python 包的 `scripts/main.py` 多为薄入口，真实逻辑在 `drugpaper/skills/*.py`。

### 11.9 上下文构建（`skills/bootstrap.py`）

- `context_for(ctx, target_ref, include_document_image=True)`：
  - 解析 ref，校验 active revision；
  - 加载 IR；
  - target 包含类型/文本/章节/caption/regions/attributes/geometry/children/subtype/page_height_pt；
  - 视觉节点按 region 裁剪图片；
  - 收集 structures、label_claims、assignments（过滤旧 revision/旧实验名来源）、compounds、current_node_refs、active artifacts（作为 upstream_versions）、text_mentions、name_sources、spans、整篇 text；
  - 工具实现包含 doc_read/search 与注册工具；
  - `markush.substitute`；
  - `SkillContext.extras` 包含 `prepare_vision`、`available_artifact_types`、`parent_run_id`、工具指纹（opsin/molparser）。
- `prepare_vision(ctx)`：要求已有 `som_image` + `molecule_boxes` Artifact 且指纹匹配，用编号图替换 `images`、原图放 `reference_images`。
- `bootstrap(services)`：注册 chem/opsin/molparser/detection/compound/figure_fact 工具；创建 `TaskRouter` + `RoutedModelClient` + `EndpointLimiter`；创建 `SkillRegistry`（snapshot_root=`blob_root.parent/skill_packages`、known_tools、known_tasks、tool_impls）；创建 `SkillRuntime` / `CapabilityIndex` / `SkillWatcher`；若设置 `DRUGPAPER_WORKBENCH_PACKAGES` 则加载本地包，否则 `watcher.reload(publish=False)`。

---

<a id="sec-12"></a>
## 12. 编排与各 Agent

### 12.1 文档流水线（`orchestration/pipeline_graph.py`）

LangGraph 节点：`ingest → mineru_parse → build_ir → render_and_index`，可选 `figure_understanding`、`full_text_understanding`。

- 状态只存引用（document_id/blob_ref/revision）；失败写 `documents.status=failed, error={code,stage,message}`。
- `run(pdf | offline_dir, force, user_id)`：
  - 非 force 且同 sha 文档已 ready → 直接返回 cached，并按需补跑 FigurePipeline/FullTextPipeline；
  - `_ingest`：创建文档、注册 pdf asset、状态 parsing；
  - `_mineru_parse`：在线调服务或读离线 `auto/`，`store_result` 建 revision，状态 parsed；
  - `_build_ir`：读 raw_layout/middle，`inspect_pdf`，`build_ir`，给图表节点补 `image_blob`，`save_ir`，状态 ir_built；
  - `_render_and_index`：按 `render_dpi` 逐页渲染并注册 page_image，状态 ready；
  - `_wrap` 捕获异常落库，记录 `timings_ms`。

### 12.2 图表流水线（`orchestration/figure_pipeline.py`）

- 图：`build_blackboard → Send(figure_agent) → compound_resolve → Send(query) → report`。
- `build_v1` 生成 targets：`scheme/table/figure`、非 removed、有 region；排序按 page、scheme 优先、local_id。
- `Coordinator.one(ref, blackboard, user_id, mode="full"|"query", query=...)`；产物 `FigureReport`。
- 文档 `quality_report.figure_agents` 记录 status/figure_count/model_calls/needs_review/timings。
- checkpoint `thread_id` 含 user/document/revision/组件 stamp（metric_dictionary、version、registry、routing、models）；force 追加 run id；可直接复用已完成快照；失败重试新 thread。

### 12.3 FigureAgent（`agents/figure/graph.py`）

#### 入口与模式

`run(node_ref, mode=full|query|review, blackboard, questions, force)`：

- `_ctx` 做 owner 校验；
- 计算 fingerprint（`molecular_contract=17`、`visual_policy_version`、`figure_exploration_*` 设置、`metric_dictionary`、ref/geometry/images/blackboard/mode/questions/version/registry/models 配置）；
- 命中 `FigureReportRow(ok|remediated)`（非 force，且 query 或 extraction 非 `unclear/not_a_table/failed`）直接复用；
- `query`/`review` 需要先有 full 报告并恢复旧 extraction；
- checkpoint `thread_id` 含 `user:figure:fingerprint[:run_id]`；
- 异常时构造 `needs_review/failed` 报告，保留已完成阶段状态；
- 有 `backfilled_extraction` 时 `persist_result` 生成 result_file；
- 最后 upsert `FigureReportRow`。

#### 阶段

| 阶段 | 行为 |
|---|---|
| `classify` | `visual_classify`；confidence<0.6 再 `visual_reclassify` |
| `plan` | `SkillRouting.resolve_chain(image_type)` 读取 routing.yaml |
| `prepare` | 保存每段原图（blob_ref/宽高/source_only）；按链执行 detect/recognize（逐 child）/som；记录模型输入编号图；生成 `_material` |
| `structure` | 探索模式调 `exploration.explore`，否则 `_structure_once` + 可选 `structure_review.review_structure`；链中必须恰好一个 vision_structuring skill；SOM 存在时编号图作模型输入、原图作 reference_images；inputs 含 text_tokens/caption/references/classification/document_evidence/information_gaps/structure_inventory/reference_substituent_evidence/regeneration_context；应用 reference assignments |
| `post` | 结构审计未通过则 `defer_delivery`；保存 `pre_backfill`；`mol_backfill`（contract=universal、structure_source_scope=current_figure、blocked_instance_paths）；写 molecule_inventory/backfilled_extraction/molecule_objects/coverage/image_products/backfill_errors/reference_bindings；表格类跑 `measurement_map`（sar_table 再加 `sar_record`）；图表类跑 `chart_measurements`；补 inchikey；multi_panel 跑 `panel_split` 并递归子 FigureAgent（`.panelNNN`）；synthetic_scheme 跑 `scheme_relations` 持久化路线；恢复审计快照 |
| `check_l1` | `validators.figure.check_all` + 生成 checklist/unchecked |
| `check_l2` | `_review`：原图 + 综合 overlay + crop，调 `figure_self_check`，产出 `ReviewResult` |
| `remediate` | 180s 总预算；`CapabilityRouter` preselect（audience=figure_agent、failed_checks、token_budget=800、invocations=action_enum、最多 4）；决策 schema 限定为披露动作；执行后重新 `_post` |
| `report` | 计算 ok 条件，汇总 open_questions，产出 `FigureReport` 并落库 |

`_next_check`：结构审计模式直接 report；`gave_up` 或 review 通过且无 error 检查 → report；round≥2 或 remediation_calls≥3 或 ≥180s → report；否则 remediate。

#### 补救动作

| 动作 | 作用 |
|---|---|
| `give_up` | 放弃本图补救，保留未解决项 |
| `apply_review_fix` | 应用审查建议的字段修补 |
| `apply_text_layer` | 用 PDF 文字层校正 |
| `fix_unit` | 修正单位 |
| `retranscribe_cells` / `rebind_labels` / `recalibrate_axes` | 一次定向 VLM 重读 + 限定字段修补 |
| `redetect` | `high_recall` 重新检测并重识别 |
| `recognize_region` | 限定 box_ids 的局部识别 |
| `reslice_table` | 重新切分表格 |
| `reclassify` | 重新分类/plan/prepare/structure |
| `split_panels` | 多面板拆分 |
| `action_grep` / `action_search` | 文档内检索 |
| `action_get_figure_report` | 读取同文档其他图表报告（同文档限制） |

#### 报告

- `ok` 条件：review 通过 + 无 error checks + 未放弃 + 无 backfill_errors + extraction 状态可用 + SAR 完整 + exploration completed。
- 状态：`remediated`（round>0）/ `ok` / `needs_review`。
- `open_questions` 汇总：exploration 未解决项、检查、审查、未检查、SAR、不可用抽取。
- `FigureReport` 字段：`artifact_ids`、`extraction`、`pre_backfill`、`backfilled`、`coverage`、`image_products`、`checks`、`review`、`unchecked`、`accounting`、`trace`、`exploration`、`query_answers`、`child_reports`、`run_id`。

#### 探索与审查

- `exploration.py`（约 1200 行）：模型驱动工具使用；限定单文档单 revision 与单个可写图表节点；工具包括浏览文档地图、搜索、预览页、查看高清图/局部图、首次提取、审查、补丁、重建、登记引用、拼接、校验、更新缺口、结束等。
- `structure_review.py`：独立交付前审查与事务性、证据绑定的结构编辑（`CoverageItem`、`StructureIssue`、`StructureAudit`、`StructureOperation`、`PatchStructure`、`GetStructure`；`get/preview/patch`；`review`；`defer_delivery`）。
- `context.py`：有界粗到细证据上下文、`document_map`、`extraction_evidence`、gap 管理、`compact_context`。
- 其他：`reference_assignments.py`（文档级类型化引用指派）、`structure_regeneration.py`、`markush_repair.py`、`assembly_tools.py`、`visual.py`、`investigation.py`、`remediation.py`、`report.py`、`state.py`、`process_trace.py`、`accounting.py`。

### 12.4 分子流水线（`orchestration/molecule_pipeline.py`）

遍历 `scheme/figure/table`（非 removed、有 region、可 targets 过滤）：

```
molecule_detect → 当前子 molecule 逐个 molecule_recognize（并发）→ som_render → label_bind → 文档级 compound_resolve
```

- `asyncio.Semaphore(4)` 限制并发；
- 任一 skill 非 completed 抛错；
- 子分子节点由 `molecule_detect` 提案创建（`{parent}.m{k:03d}`），单分子重识别按 work unit 缓存。

### 12.5 正文流水线（`orchestration/text_pipeline.py`）

- 遍历 reading_order 中的 paragraph/list，`section_role` 在抽取角色集合或 `introduction`；
- 每个节点 `text_extract`（work unit 按节点）；
- experimental 节点再跑 `name_to_structure`；
- 文档级 `compound_resolve`；
- `project_text_measurements`；
- 每页 `load_page_pack`；
- 状态：`failed/ready/needs_review`；`p6_complete=False`（需全文流程完成）。

### 12.6 Reader（`agents/reader/graph.py`）

- `ReaderProgress`：`status=completed|pending|failed`、`notes`、`last_page`、`pending_refs`、`checkpoint_id`、`run_id`、`error`。
- `_restore(ctx, from_page)`：按 user/doc/revision/fingerprint/active 找最近 checkpoint；`from_page` 时把该页及之后置 inactive，且要求存在上一页 checkpoint。
- `_flush`：`open_questions` 每 8 个一批调用 DocLookup，累计 model/tool calls 与证据 pointer。
- `_checkpoint`：重新 load_context 校验 fingerprint；Document 行 `FOR UPDATE`；要求 predecessor 仍是 latest（并发保护）；version=max+1；写 `ReaderNoteRow`（schema 0.6.1、notes、deltas、page_mode、predecessor）。
- `read(document_id, from_page, wait_timeout_s)`：
  - 逐页 `build_page_pack`；未 ready 时最多等待 60s 并重载上下文（依赖变化则报错）；
  - `read` 模式每 unit 调 `reader_page` 生成 `NoteDelta`，`merge_delta` 校验证据，更新 open_questions、compact；
  - `skim` 模式只记录 pointer；
  - 每页 checkpoint；
  - 最终写 `RunRow(run_type=reader, component=reader_read, metrics model/tool_calls/pages_read)`。

### 12.7 DocLookup（`agents/lookup/batch.py`）

`lookup(questions 1..8, ctx)`：

1. 校验 fingerprint；
2. 创建 `RunRow(run_type=lookup, component=doc_lookup)`；
3. 按 `cache_key` 去重，查 cached（非 need_vision 直接命中）；
4. `_execute`；
5. 再次校验 fingerprint；
6. 未完成项按 kind 返回 `need_vision/not_found`；
7. 非 need_vision 结果写 `LookupRow`。

`_execute`：

- 创建 lookup_tools；
- vision 类按 `target_page` 分组；
- 其他先走 `fast_action`（确定性快路径），结果 `valid_evidence` 校验；found 无证据降为 not_found；
- 其余问题一次 `lookup_plan`（每题最多 2 个只读动作，最多 20 次工具调用，按关键词披露 `query_measurements/get_compound/get_figure_report/get_table/get_page_text` 等工具）；
- 一次 `lookup_answer`（每题仅用本题工具结果；found 必须有证据）；
- 再对 need_vision 页调 `vision_page`；
- 所有答案带 `lookup_run_id`。

模块：`cache.py`（确定性缓存键）、`fast_path.py`（快路径）、`models.py`（LookupQuestion/Result/BatchResult/Action/Plan/Answers）、`tools.py`（只读工具）、`vision.py`（单次无工具视觉请求，硬时限）。

### 12.8 全文流水线（`orchestration/full_text_pipeline.py`）

图：`reader_read →（status==completed）path_reason → step_builder →（checks 且 attempts<3）重试 path_reason，否则 project`。

- `_read`：`ReaderAgent.read`、`load_context`；
- `_reason`：校验 fingerprint 未变，`synthesize(..., feedback)`；
- `_build`：`build_steps` + `validate_path`，写 `RunRow(run_type=validation, component=path_validate)`；
- `_project`：`project` + `validate_projection`，汇总 warnings/needs_review，状态 `needs_review` 或 `completed`。
- `run(..., prepare_text=True, force=False, from_page=None)`：
  - 先 `TextPipeline`；
  - 再 `project_text_measurements`；
  - 创建 `RunRow(run_type=pipeline, component=full_text)`；
  - 提交 `FullTextResultRow(revision, fingerprint, status, payload)`；
  - 写 run.model/metrics（synthesis_attempts/model_calls/tool_calls）。

### 12.9 导出（`exporters/package.py`）

`create_export` 校验当前 revision+fingerprint 的 `FullTextResultRow`；`validate_projection` 的 V-J01/V-J03/V-E01 阻断；生成 ZIP：

| 文件 | 内容 |
|---|---|
| `result.drug_optimization_v1.json` | 交付 JSON |
| `result.drug_optimization_v1.audit.json` | 审计孪生（证据、来源） |
| `document.json` | 文档元信息 |
| `compounds.json` | 化合物台账 |
| `images/*` | 图表/分子图 |
| `paper.corpus.md` / `paper.audit.md` | 全文与审计文本 |
| `measurements.csv` | 测定表 |
| `charts/*.csv` + `overlay.png` | 图表数据与叠加图 |
| `chunks.jsonl` | 页/节点分块 |
| `manifest.json` | format_version 0.6.1、app_version、profile、revision、fingerprint、result_id、status、components 全文件哈希、artifacts、models、call_metrics、checks、warnings、files 哈希 |

固定 zip 时间戳保证可复现；导出前再次校验 fingerprint；写 BlobStore + `ExportRow`；返回 `export_id/blob_ref/files/warnings/download_url`。

---

<a id="sec-13"></a>
## 13. 事实、化学与校验

### 13.1 事实投影（`facts/`）

| 模块 | 职责 |
|---|---|
| `compounds.py` | Compound、StructureCandidate、RGroupAssignment 等事实模型 |
| `labels.py` | 标签归一 |
| `resolver.py` | 编号/别名/候选优先级与冲突解析 |
| `projector.py` | Artifact → 化学事实（同事务；失败整份回滚） |
| `figure_models.py` | 表格/曲线/反应/结构等 Pydantic 模型（Cell/TableGrid/Assay/Measurement/Axis/Series/ChartDataset/MoleculeReference/ReactionStep/ReactionTransformation/ReactionPathway/ExtractionGap/VisualExtraction/ReviewItem/ReviewResult） |
| `figure_projector.py` | 图表事实投影 |
| `figure_result.py` / `flowchart_delivery.py` | 结果文件与流程精简结果 |
| `legacy_backfill.py` | 十类图片输出的 E-SMILES 回填 |
| `table_processing.py` / `table_transcription.py` | 表格网格化与单元格转录 |
| `text_extraction.py` | 正文抽取、区间提及、V-E02、拒绝无原文依据字段 |
| `text_ledger.py` / `text_measurements.py` | 文本台账与测定 |
| `name_extraction.py` / `name_sources.py` | 实验名称抽取与来源追踪 |
| `metric_dictionary.py` | 指标字典加载、同义词、单位类 |
| `units.py` / `value_parser.py` | 单位换算与数值解析 |
| `paths.py` | 优化路径提议与步骤构建 |
| `reference_delivery.py` / `molecular_delivery.py` | 引用与分子交付形状 |
| `reaction_semantics.py` | 反应语义 |
| `assays.py` / `measurements.py` | 实验条件与测定 |
| `compound_instances.py` / `compound_series.py` | 实例/系列化合物 |
| `label_geometry.py` | 标签几何 |
| `full_text.py` | 全文上下文与 fingerprint、证据校验（见 13.3） |

**`projector.project` 关键规则**（`facts/projector.py`）：

- 仅处理 `molecule_detect`、`molecule_recognize`、`mol_backfill`、`markush_assemble`、`compound_resolve`、`sar_record`、`name_to_structure`；
- 校验文档 active_revision；校验正文归一材料/结构候选/几何/子节点集合未变；
- `node_proposals`：创建/更新分子子节点（局部坐标 → 页坐标），移动或删除时把旧结构候选置 inactive；父节点 children 只替换 `.m` 子项，不触碰兄弟分子；
- 同一 Work Unit 的旧事实保留为历史（按 artfact_id 置 `is_active=False`，`chemistry_conflict` 只对重新计算的 compound 置失效）；
- `molecule_update`：只允许目标分子或其子分子，禁止越权/锁定；
- `compound`：校验文档归属；更新台账、accepted_structure_id、别名增删；
- `structure_binding`：校验结构与化合物归属后绑定；
- `structure_candidate`：目标必须是 target 或其子孙；E-SMILES roundtrip；必须有 primary 证据；
- `label_claim`：校验节点与结构候选；label 归一；
- 所有事实写入校验 `owned_node`（user/doc/revision 一致）。

### 13.2 化学层（`chem/`）

| 模块 | 职责 |
|---|---|
| `normalize.py` | SMILES/结构规范化 |
| `esmiles.py` | E-SMILES 拆分/解析、roundtrip 校验 |
| `splicer/` | Markush 替换库（约 1720 行）：labeled_ring、positional、adapter；R 基替换 |
| `markush.py` | Markush 通式与 R 基组装 |
| `assembly.py` / `assembly_inputs.py` / `assembly_compat.py` | 片段拼接、输入构建、兼容性 |
| `diff.py` | MCS 原子级差异、区域替换、虚拟中间体 |
| `formula.py` | 分子式与 HRMS |
| `groups.py` / `ring_lexicon.py` | 基团缩写、环系词典 |
| `opsin.py` | OPSIN 客户端（名称→结构） |
| `roles.py` | 化学角色 |
| `backfill.py` | 结构回填 |
| `change_classifier.py` | 变化类型分类 |
| `instance_delivery.py` / `product_candidates.py` | 实例交付与产物候选 |

### 13.3 全文上下文（`facts/full_text.py`）

- `component_stamp`：`drugpaper/**/*.py|md|yaml|json` + `prompts/skills` + `config yaml` 的内容摘要，用于 fingerprint。
- `FullTextContext`：user/ir/component_version/compounds/structures/measurements/assays/reports/extractions/artifacts；fingerprint = sha256(version p6.1 + 全部字段)。
- `load_context` 只纳入 active artifact（completed/validated/needs_review/human_corrected）、节点仍存在、`text_extraction`/`name_to_structure` 的 `source_text_sha256` 与当前节点一致、OPSIN 名称来源仍 current；过滤无效证据；只取 `mode=full` 的 FigureReport。

### 13.4 校验（`validators/`）

| 模块 | 规则 |
|---|---|
| `figure/check_all` | 汇总图表检查：content、numeric、structure、chart、checklist；输出码与 `unchecked` |
| `path.py` | V-P01..V-P08：主路线编号真实存在且从先导到最终产物；端点必须有同一句优化关系/箭头证据；完整结构缺失或 MCS 超时；变化区域不是一个；声明变化类型与计算不一致；直接优化链；叙述顺序；OPSIN 一致 |
| `projection.py` | V-J01 schema；V-J02 先导/步骤连续性；V-J03 E-SMILES 与存储原文一致（阻断导出）；V-J04 来源顺序与指标值不含编号；V-J05 解释字段中文比例；V-E01 非空事实必须有与交付值一致的当前 primary 证据 |
| `workflow.py` | `RequiredCallsValidator`：关键步骤必须调用指定工具/Skill |

### 13.5 交付投影（`profiles/projector.py`）

- 投影为固定 legacy 交付形状 + 带证据的 audit 孪生；
- `export_smiles`、`measurement_text`、assay/conditions/rank/subject；
- 前端“全文结果”视图按 `projection.delivery` + `projection.audit` 递归展示，字段叶子挂证据链接。

---

<a id="sec-14"></a>
## 14. 工作台后端（`workbench/`）

### 14.1 目录布局（`workbench/store.py`）

工作区根：`blob_root.parent / "workbench" / digest(["default", document_id, revision])`。

```
root/
├── figures/<digest(node_ref)>/     # 单图局部文件、基线、快照
├── runs/<wd_...>/                  # 单次运行
│   ├── run.json                    # initial_state / 参数
│   ├── settings.json               # 模型/路由/工作区覆盖
│   ├── control.json                # pause 请求
│   ├── execution.lock              # fcntl 独占
│   ├── events/000001.json ...      # 逐步事件
│   ├── states/<stage>.json         # 每阶段状态
│   ├── final-result.json           # post/report 结果
│   ├── delivery-result.json        # 交付结果
│   └── slots/                      # 并发槽
└── ...
```

- `safe_path`：拒绝绝对路径、`..`、逃逸根目录、符号链接。
- `editable(name)`：白名单后缀 `.py/.md/.json/.yaml/.yml/.txt`，目录限 `drugpaper/skills/`、`drugpaper/tools/`、`drugpaper/chem/`、`drugpaper/vision/`、`prompts/`，或 `config/routing.yaml`。
- `alive(pid)`：Linux 下检查 `/proc/<pid>/stat` 非 zombie。
- `run_dir`：run_id 必须匹配 `wd_[A-Za-z0-9]+`。
- `materials_stamp`：对目标节点及其子节点的 id/geometry/status/structure_id 做摘要，判断材料是否变化。

### 14.2 快照与复用（`snapshot.py`、`reuse.py`）

- `snapshot`：把工作区的 `skills`、`models`、`routing` 覆盖写入运行目录，保证运行可复现；校验时按快照恢复。
- `reuse.visual_extraction_only`：判断改动是否仅影响下游视觉抽取，从而允许复用上游检测/识别结果。

### 14.3 隔离运行器（`workbench/runner.py`）

启动：`python -m drugpaper.workbench.runner <run_dir>`。

`execute(directory)`：

1. 读取 `run.json` / `settings.json`；
2. 创建 Services / Recorder；
3. `fcntl` 独占 `execution.lock`；
4. 在 `slots/` 获取并发槽（PostgreSQL 最多 `figure_concurrency` 且 ≤3；SQLite 1）；
5. 校验文档 revision；
6. 替换模型工厂为 `RecordingClient`；
7. 恢复 `initial_state`，重置本次 accounting；
8. 写 `RunRow(run_type=workbench, component=figure_debug)`；
9. 按 `STAGES` 从 `start` 开始逐阶段执行：
   `classify → plan → detect → recognize → som → structure → post → check_l1 → check_l2 → report`；
10. 支持 `control.json` 暂停、`pause_before`、`stop_after`、人工 image_type、指定 vision structuring skill、detect/recognize 复用、人工 SMILES 优先；
11. `post/report` 持久化 `final-result.json` 与 `delivery-result.json`；
12. 每阶段写 `states/{stage}.json` 与 materials_stamp；
13. `report` 阶段写 `FigureReportRow`；
14. 异常置 `failed` 并标记 running 步骤；
15. `finally` 更新 RunRow、释放槽/锁、`aclose` Services。

事件：`Recorder` 把事件写 `events/{index:06d}.json`；base64/data URI 图片先存 BlobStore 换成 `blob_ref`；`RecordingClient` 记录 `model_request` / `model_response` / `model_error`；`DebugAgent` 继承 FigureAgent，记录 exploration/tool/skill 事件，`debug_code` 进入指纹。

---

<a id="sec-15"></a>
## 15. Worker / Celery（`workers/`）

- `celery_app.py`：Redis URL 派生 broker db 11 / backend db 12；无 Redis 时用 memory；`acks_late=True`、`reject_on_worker_lost=True`、`prefetch_count=1`、`visibility_timeout=86400`、默认队列 `batch`、JSON 序列化。
- `tasks.py`：
  - `understand_full_text`：`Services.from_settings(user_id)` → `FullTextPipeline` → `aclose`；
  - `process(document_id, user_id, batch_id)`：校验文档归属；非 ready 时取 PDF 跑 `DocumentPipeline(understand_figures=True)`，ready 直接 `FigurePipeline`；batch 行加锁记录 results，按全部文档完成情况置 `completed/failed/running`；异常时 batch 记 failed；
  - `process_document`：Celery 任务包装。
- 批次 API 使用 `process_document.apply_async`；派发失败标记 `dispatch_failed`。

---

<a id="sec-16"></a>
## 16. 追踪、运行记录与监控

### 16.1 `runs` 表与追踪器（`tracing/`）

- 顶层 Run：`run_type`（`pipeline|reader|lookup|validation|workbench|figure_debug` 等）、`component`、`trace_id`、`parent_run_id`、`status`、`metrics`、`error`、`started_at/ended_at/duration_ms`。
- 子事件：`model_call`、`tool_call`、`validation`、`budget_exhausted`、`cancelled`，部分实现为子 Run（带 parent_run_id）。
- `DbTracer`：事件落库；`JsonlTracer`：每 run 一个 jsonl（字符串摘要 200 字）；`CompositeTracer`；`NullTracer`；`ConsolePrinter`。
- `Settings.trace_jsonl` 控制是否同时写 `runs/<run_id>.jsonl`。

### 16.2 监控 API（`api/monitoring.py`）

- `/documents/{id}/runs`：可按 status/component/trace_id 过滤；分页；覆盖全部 revision。
- `/documents/{id}/runs/{run_id}`：完整详情；凭据与长字段脱敏（`api_key`、`authorization`、`password`、`secret`、`token` 等）。
- `/documents/{id}/reader-notes`：当前 revision active 检查点。

### 16.3 如何定位一次失败

1. 前端“运行监控”按 component 过滤（如 `figure_agent`、`reader_read`、`doc_lookup`、`path_validate`）。
2. 看 Run `metrics`（model_calls/tool_calls/pages_read/synthesis_attempts）与 `error`。
3. 看 Artifact `status`、`input_fingerprint`、依赖；看 `skill_proposals`。
4. 图表问题看 `FigureReportRow.checks/review/open_questions/trace`；全文问题看 `FullTextResultRow.status/payload.warnings` 与 `/full-text` 的 `checks`。
5. 工作台单图问题看 run 目录 `events/*.json`、`states/*.json`、`process_error`。

---

<a id="sec-17"></a>
## 17. 已知限制与边界

1. **无鉴权/无用户隔离**：所有数据 `user_id='default'`；仅应绑定 127.0.0.1 或可信内网。P7 待做。
2. **工作台仅开发用**：允许编辑后端 Skill/工具代码并运行隔离子进程，必须单机可信环境使用。
3. **外部服务可选**：未配置 MinerU/MolParser/OPSIN/YOLO 时，相关链路返回 pending/failed；离线导入与已有数据浏览不依赖外部服务。
4. **全文链路未完全验收**：P5 整体验收、部分样例名称/HRMS、十篇独立预测仍未通过；`p6_complete` 保持 False。
5. **模型输出不可信**：所有结构化结果必须经 Schema、证据、白名单与业务校验；失败保留 `needs_review`，不自动“修好”。
6. **SQLite 与 PostgreSQL 行为差异**：SQLite 无 `tsvector`/`trgm`/递归 CTE/advisory lock，部分能力退化为 Python 实现；并发槽数量也不同。
7. **PDF 解析质量取决于 MinerU**：OCR 行、跨页合并、章节角色都可能需要人工校正；工作台提供校正入口。
8. **导出可复现性**：固定 zip 时间戳，但内容哈希仍取决于模型/配置版本；manifest 记录组件哈希以便核对。

---

<a id="sec-18"></a>
## 18. 扩展开发指南（Recipes）

### 18.1 新增一个 prompt 型 Skill

1. 建目录 `drugpaper/skills/builtin/<name>/`；
2. 写 `manifest.yaml`：`name` 与目录一致、`kind: prompt`、`model_tasks`、`input_schema`/`output_schema`、`accepts`、`produces`、`budgets`、`disclosure`；
3. 写 `SKILL.md`：frontmatter `description` 为 L0；固定小节（何时使用/何时不用/输入/输出/代价与限制）；
4. 写 `prompts/*.md`（Jinja2，StrictUndefined），可 `include` `prompts/common/*`；
5. 写 schemas；至少一个 `tests/cases/*.json`（可带 `*.response.json` 回放）；
6. 把 Skill 加入 `config/routing.yaml` 对应 subtype 链，或在代码中通过 `SkillTool` 调用；
7. 运行 `POST /api/v1/skills:reload`（带 admin token）或重启；
8. 用 `GET /api/v1/skills/{name}/card` 与 `/versions` 检查；
9. 工作台选一张图，指定该 Skill 试跑。

### 18.2 新增一个 python 型 Skill

1. 同上建包，`kind: python`、`entry: scripts/main.py:<func>`；
2. 入口接收 `inputs` + `SkillContext`，返回 `SkillResult`；
3. 产出 `ArtifactDraft`（必须恰好 1 个）；需要节点/事实时用 `NodeProposal`/`FactProposal`；
4. 若需要新事实类型，先扩展 `facts/*` 模型、`FACT_MODELS` 与 `projector.project`，并补迁移；
5. 在 `validate_result` 白名单中允许新的 `artifact_type`/fact type（如适用）；
6. 写测试用例并注册。

### 18.3 新增一个工具

1. 在 `drugpaper/tools/` 新建模块，用 `@tool` 定义 async 函数与 Pydantic 输入模型；
2. 在 `skills/bootstrap.py` 注册到 `ToolRegistry`，并加入 `known_tools`；
3. 若 Skill 需要调用，写入其 `manifest.tools` 白名单；
4. 工具必须返回 `ToolResult`，不要把业务异常抛给 Agent；
5. 注意 `max_output_chars` 与 `refs`，避免大结果撑爆上下文。

### 18.4 新增一个 API

1. 在 `drugpaper/api/` 新建 router，使用 `get_services`/`get_session` 依赖；
2. 错误用 `ApiError`，统一格式；
3. 在 `api/app.py` 挂载；
4. 如需分页/上限，显式定义；
5. 如需前端消费，更新前端 `api.ts`/视图（见前端 Codemap）。

### 18.5 新增模型任务

1. `config/models.yaml` 增加 `tasks.<name>`（stage/pool/model/image_max_side/timeout/重试等）；
2. 在 Skill manifest 的 `model_tasks` 中声明；
3. 代码中通过 `TaskRouter`/`RoutedModelClient` 调用；
4. 工作台调试可用 `RecordingClient` 查看 request/response。

### 18.6 修改数据模型

1. 改 `persistence/models.py`；
2. 新增 Alembic 迁移（不要改历史迁移）；
3. 同步 `domain/*` Pydantic 模型、`api/schemas.py`、前端 `contracts.ts`；
4. 补 `tests/unit/test_migrations_sqlite.py` 与仓储测试；
5. 注意 PostgreSQL/SQLite 差异与 `user_id` 默认值。

### 18.7 新增校验码

1. 在 `validators/` 对应模块新增 `CheckResult(code=..., dimension=..., severity=..., field_path=..., message=...)`；
2. 若阻断导出，在 `exporters/package.py` 明确列出；
3. 若进入 FigureAgent 补救，确保 `CapabilityRouter` 能按失败码匹配动作；
4. 前端会按 severity/code 展示，无需硬编码。

---

<a id="sec-19"></a>
## 19. 关键不变量（修改代码前请确认）

1. 同一 `work_unit_key` 任一时刻至多一个 active Artifact；新版本必须 supersede 旧的。
2. Artifact 缓存命中必须指纹一致且状态非 failed；`failed` 结果不能当作缓存。
3. 证据必须属于当前文档当前 revision、节点存在且非 removed；化学事实必须有 primary 证据。
4. 正文类 Skill 提交前必须确认 revision/text_raw/section_role 未变；分子检测需确认父节点几何与子节点集合未变。
5. 事实投影与 Artifact 写入必须同事务；失败整份回滚，不能留下半份事实。
6. Reader checkpoint 必须绑定 fingerprint 与 predecessor，且只允许最新链；重读会把该页及之后置 inactive。
7. 全文结果与导出必须绑定 revision+fingerprint；不匹配时 404/阻断，不能导出旧结果。
8. 图表报告缓存键必须包含组件版本、模型配置、指标字典、registry/routing、图几何/图像、模式与问题；`force` 才跳过复用。
9. 工作台运行必须在隔离子进程与文件锁/并发槽下执行；文件改动要映射到最早受影响阶段。
10. 任何跨文档/跨 revision 引用都要在写入前重新校验归属；不能信任模型给的引用。
11. API 当前无鉴权；不要把服务暴露到不可信网络。

---

<a id="sec-20"></a>
## 20. 附录：常用符号索引

| 符号 | 位置 | 用途 |
|---|---|---|
| `DocumentPipeline` | `orchestration/pipeline_graph.py` | PDF → IR → 页面图 |
| `FigurePipeline` | `orchestration/figure_pipeline.py` | 全文档图表理解 |
| `Coordinator` | `agents/coordinator/` | 黑板、分发、汇总 |
| `FigureAgent` | `agents/figure/graph.py` | 单图阶段式 Agent |
| `ReaderAgent` | `agents/reader/graph.py` | 逐页阅读 |
| `DocLookup` | `agents/lookup/batch.py` | 批量回查 |
| `FullTextPipeline` | `orchestration/full_text_pipeline.py` | 阅读→综合→路径→投影 |
| `SkillRuntime` | `skills/runtime.py` | Skill 执行、缓存、提交 |
| `SkillRegistry` | `skills/registry.py` | 注册、激活、解析 |
| `load_package` | `skills/loader.py` | 包校验 |
| `CapabilityRouter` | `capabilities/router.py` | 补救动作预选 |
| `DisclosureToolset` | `capabilities/toolset.py` | 渐进披露工具集 |
| `SkillTool` | `capabilities/toolset.py` | Skill 作为工具 |
| `ToolRegistry` | `tools/registry.py` | 工具调用与容错 |
| `TaskRouter` / `RoutedModelClient` | `gateway/task_router.py` | 模型任务路由与回退 |
| `MoleculeDetectionService` | `detection/service.py` | 本地单线程检测队列 |
| `RemoteDetectionService` | `detection/remote.py` | 共享 Worker 检测 |
| `create_export` | `exporters/package.py` | ZIP 导出 |
| `execute` | `workbench/runner.py` | 隔离单图运行器 |
| `build_ir` | `parsers/mineru_legacy/adapter.py`（`builder`） | MinerU → IR |
| `save_ir` / `load_ir` | `persistence/repos/nodes.py` | IR 持久化 |
| `create_version` | `persistence/repos/artifacts.py` | Artifact 版本 |
| `valid_evidence` | `facts/full_text.py` | 证据校验 |

---

<a id="sec-21"></a>
## 21. 参考

- 阶段代码地图：`docs/codemap/phase-1-codemap.md` … `phase-6-codemap.md`
- 冻结接口：`docs/interfaces/`（skills-and-capabilities、chemistry、figure-agents、full-text 等）
- 开发计划：`docs/dev-plan/`（含 P8 early workbench）
- 部署：`deploy/README.md`、`services/*/README.md`
- 前端：`web/README.md`、`docs/codemap/frontend-codemap.md`
