# 接口冻结：Document IR 与文档 API（P2）

2026-11-20 实现补充：以下需求说明保留确认时的基线；基础调试能力已通过独立 `/documents/{id}/workbench` 接口接入，详见 [监控文档的工作台接口](monitoring.md#基础工作台接口2026-11-20)与 [P8 最新交付记录](../dev-plan/execution/phase-08-execution.md#2026-11-20-基础版本实现与验证记录)。原冻结接口和完整阶段验收结论不变。

> 状态：**已冻结**（P2 结束，2026-09-30）。修改以下模型、ID 规则或 API 需要在合并请求中说明影响范围，并通知 P3/P4/P5/P8 负责人。
> 权威定义以源码为准：`drugpaper/domain/{ids,geometry,nodes}.py`、`drugpaper/api/`；OpenAPI 见 `docs/interfaces/openapi-p2.json`。
> P1 冻结的 `DocumentSource` 协议不变，P2 的 `IrDocumentSource` 实现它（见 `agent-runtime.md`）。

## 2026-11-20 默认布局展示需求（待实现核对）

依据[总体设计 17.6](../drug-paper-agent-detailed-design.md#176-开发调试工作台已确认需求2026-11-20待实现)：默认渲染 MinerU 最终合并单元，图表 body/caption/footnote 原始成员不重复叠加；独立正文保留并可查看 OCR。使用 merged_from/belong_to 及跨页成员关系映射，不按 image_body 类型一刀切；原始响应与成员证据仍保留。跨页图表以同一逻辑单元的多个 page region 展示，不新增父子图操作。

下文 P2 纯文本合并组解散、原始成员子节点及 ID 是现有 IR 规则。本次需求不直接改其冻结定义；实施时优先提供最终布局展示投影/成员映射，核对纯文本组合并展示，确需改变 IR 时另行版本化。手动上传在 MinerU/IR/渲染后停止，不触发图表分类或分子步骤。

## 1. 节点 ID 规则

| 对象 | 格式 | 示例 |
|---|---|---|
| Document | `doc_{ULID}` | `doc_01M3SJ9A7RRTZADK36DWXFKT80` |
| 节点全局引用 | `{document_id}/r{revision}/{local_id}` | `doc_01M3…/r1/p003.g000.c0` |
| 原始块 | `p{page:03d}.b{detailed_index:03d}` | `p000.b007` |
| 页内合并组 | `p{page:03d}.g{confirmed_index:03d}` | `p003.g000` |
| 跨页合并组 | `x{首页:03d}.g{confirmed_index:03d}` | `x007.g001` |
| 图注 / 脚注 / 图内部件 | `{parent}.c{k}` / `{parent}.f{k}` / `{parent}.p{k}` | `p003.g000.c0` |
| 句首小标题（run-in） | `{paragraph}.h0` | `p001.b006.h0` |
| 丢弃块（页眉页脚，F9） | `p{page:03d}.d{k:03d}` | `p000.d000` |
| 章节 / 前置信息 / 文档根 | `s{k:03d}` / `front` / `doc` | `s002` |
| 分子（P4）/ Panel（P5） | `{parent}.m{k:02d}` / `{parent}.k{k:02d}` | `p003.g000.m02` |

- ID 在同一 revision 内不可变；阅读顺序调整只改 `reading_order`。
- 与 P1 的 `LayoutJsonSource` 块 ID 一致：P1 的引用（`p000.b007`）在 IR 中仍然有效。
- 空文本块（F5）不生成节点，它的 bbox 作为续段 region 挂到拥有其文字的段落上。
- 纯文本合并组（组类型为 text 且没有图注标签）被解散，成员按原始块 ID 生成节点。

## 2. 坐标

```python
class BBox(BaseModel):   x0: float; y0: float; x1: float; y1: float   # 归一化 [0,1]，左上原点

class Region(BaseModel):
    page: int
    bbox: BBox                                    # 页面归一化坐标（按 PDF 实际页面尺寸归一化）
    source_bbox_pt: tuple[float, float, float, float] | None   # 原始 PDF 点坐标
    role: Literal["primary", "continuation", "segment"] = "primary"
    derived: bool = False                         # 由启发式推导（没有行级证据），UI 中需可见

class Geometry(BaseModel):
    regions: list[Region]                         # 续段、跨页 segment 可有多个；第一个为主 region
    local_bbox: BBox | None = None                # 子节点相对父 region 的坐标（P4 起为权威值）
    parent_region_index: int | None = None
    derived: bool = False
```

换算函数（`drugpaper/domain/geometry.py`）：`pt_to_norm`、`norm_to_pt`、`norm_to_px`、`local_to_page`、`page_to_local`（往返误差 < 1e-6）、`iou`、`overlap_ratio`、`union_bbox`。

## 3. 文档节点

```python
NodeType = Literal["document", "front_matter", "section", "heading", "paragraph", "list",
                   "figure", "scheme", "table", "chart_panel", "figure_part", "molecule",
                   "caption", "footnote", "formula", "reference_entry", "discarded", "unknown"]

class DocumentNode(BaseModel):
    document_id: str; revision: int; local_id: str
    node_type: NodeType
    subtype: str | None          # figure: toc_graphic …；heading: title / run_in；paragraph: authors / citation；
                                 # figure_part: image / table / text / contained_visual
    parent_id: str | None        # 局部 ID；discarded 节点为 None（不在主树）
    children: list[str]          # 局部 ID，按阅读顺序
    reading_order: int           # 全文先序遍历编号，唯一
    geometry: Geometry
    label: str | None            # "Table 1"、"Scheme 3"、"Figure 2"（P4 起：分子标签 "26"）
    text_raw: str | None         # MinerU 原文（展示与证据）
    text_norm: str | None        # 规范化文本（检索与 grep）
    section_role: str | None     # front_matter / abstract / introduction / results / discussion / experimental /
                                 # conclusion / references / acknowledgments / supporting_information /
                                 # abbreviations / author_information / body
    source: Literal["mineru", "skill", "human"]
    source_ref: dict             # page_idx、detailed_index、confirmed_index、block_type、bbox_pt、merged_from、
                                 # legacy_id（fig_p{page}_{ci} / tab_p{page}_{ci}）、caption_text、extra（未知字段）
    quality_flags: list[str]     # absorbed_by_merge / caption_mismatch / caption_absorbed_text / derived_continuation /
                                 # contained_in_larger_box / bbox_out_of_bounds / asset_missing
    status: Literal["active", "removed", "needs_review"]
    attributes: dict             # 见下表
```

| attributes 键 | 出现在 | 含义 |
|---|---|---|
| `lines` | 段落、列表、标题、图注 | 行级 bbox `[{page, bbox:[x0,y0,x1,y1]}]`（来自 middle.json，供证据高亮到行） |
| `continuation_blocks` | 段落、列表、图注 | 被认领的空文本块 ID |
| `image_path` / `image_blob` | 图、表、路线图 | MinerU 裁剪图的相对路径 / blob_ref（F1：表格内容只有图片） |
| `footnote_marks` | 表、路线图 | `{a: "...", b: "..."}` |
| `title_level` / `level` | 标题 | MinerU 的 title_level / 修正后的层级（1 章节、2 小标题、3 句首小标题） |
| `run_in_heading` | 段落 | 对应的 `.h0` 小标题节点 |
| `norm_version` | 有文本的节点 | 规范化规则版本（`drugpaper/text/normalize.NORM_VERSION`） |

`text_norm` 与 `text_raw` 之间的偏移映射不入库：规则确定，按 `norm_version` 随时重算（`normalize_detail(text_raw).map_span`）。

## 4. 证据引用

```python
class EvidenceRef(BaseModel):
    node_id: str                          # 全局节点引用
    field_path: str | None = None         # 表格 "cells[3][5]"（P5 起）
    char_span: tuple[int, int] | None     # text_raw 坐标
    regions: list[Region] | None          # 精确高亮区域；缺省用节点 regions
    artifact_id: str | None = None
    role: Literal["primary", "context", "definition", "condition", "contradiction"] = "primary"
```

问答结果 `AgentRunResult.evidence`（P2 新增字段）是 EvidenceRef 的 dict 形式；grep 的 `char_span` 可直接作为证据。

## 5. 其他模型

- `Relation(source, target, type ∈ {contains, caption_of, footnote_of, continuation, absorbed_from}, evidence, status)`
- `QualityReport(issues[QualityIssue(code, severity, node_id, page, message, details)], page_count_layout, page_count_pdf, scanned_pages, middle_json_used)`
- `DocumentMeta(title, authors, doi, doi_source ∈ {text, filename}, journal, year, volume, pages)`
- `IRDocument(document_id, revision, parser, parser_version, ir_version, pages[PageInfo], nodes, relations, meta, quality)`
- 构建入口：`build_ir(layout, middle=None, *, document_id, revision, source_name, pdf_page_count, scanned_pages, available_assets, pdf_page_sizes) -> IRDocument`

## 6. 检索

| 能力 | 入口 | 说明 |
|---|---|---|
| grep | `GrepEngine(nodes).grep(GrepQuery)` | 设计文档 7.9.3 节的输入输出；覆盖正文、标题、图注、脚注、列表、图内文字；References 默认不检索 |
| fts | `PostgresSearch` / `InMemorySearch` | PG：`nodes.tsv`（`to_tsvector('simple', label‖text_norm)`）+ `websearch_to_tsquery`；AND 语义 |
| trgm | 同上 | PG + pg_trgm：`word_similarity ≥ 0.3`；无 pg_trgm 时 Python 回退（同一阈值） |
| hybrid | 同上 | 两路分数归一化后 0.6·fts + 0.4·trgm |

## 7. Agent 工具（IR 版）

`get_outline`、`grep`、`search`、`get_node`、`get_children`、`get_context_window`、`get_section`、`get_page_text`。返回文本为 `text_norm`，`ToolResult.refs` 为节点全局引用。装配：`build_ir_qa_agent(source, model, tracer, budget, search_backend, checkpointer)`。

## 8. API（前缀 `/api/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents` | multipart `file=`；202 `{document_id, status, duplicate}`；后台运行管线 |
| GET | `/documents?status=&limit=` | 列表 |
| GET | `/documents/{id}` | 状态（uploaded/parsing/parsed/ir_built/ready/failed）、元数据、质量报告摘要、错误 |
| GET | `/documents/{id}/tree` | 轻量树（不含全文） |
| GET | `/documents/{id}/pages/{p}/image?dpi=` | 页面 PNG（36～300 dpi） |
| GET | `/documents/{id}/pages/{p}/nodes?include_discarded=` | 页内节点与 regions（Overlay） |
| GET | `/nodes/{node_ref}` | 节点详情（全文、regions、子节点、属性） |
| GET | `/nodes/{node_ref}/crop?dpi=&region=&pad=` | 裁剪 PNG（按 `(pdf_sha, page, bbox, dpi, pad)` 缓存） |
| GET | `/documents/{id}/search?q=&mode=grep\|fts\|trgm\|hybrid&limit=&literal=` | 检索 |
| GET | `/blobs/{ref}` | Blob 代理 |

`node_ref` 是全局引用（含 `/`，直接拼在路径中，也接受 URL 编码）。错误格式：`{"error": {"code", "message", "details"}}`（`NOT_FOUND`、`NOT_READY`、`BAD_NODE_REF`、`NOT_PDF`、`BAD_PATTERN`、`VALIDATION_ERROR` …）。

**本阶段 API 没有鉴权与用户隔离**（表结构已预留 `user_id`，P7 启用），只应在本机或受信任的内网中运行。

## 9. 后续阶段的替换 / 扩展点

| 内容 | 现在 | 后续 |
|---|---|---|
| 表格单元格、图内文字的 grep | 未覆盖 | P5 产生 `table_grid` 等 Artifact 后加入 `SEARCHABLE_TYPES` |
| 管线执行 | 同步调用（API 后台任务） | P5 Celery 任务 + Postgres checkpointer |
| `artifacts` / `artifact_dependencies` | 已建表，未使用 | P3 SkillRuntime |
| 用户隔离 | `user_id='default'`，会话设置 `app.current_user_id` | P7 RLS |

### 文本公式预览（2026-11-20）

节点详情 `/nodes/{node_ref}` 在 `attributes.ocr_lines` 提供 `[{spans: [{type, content}]}]`，用于右侧正文/公式混排。读取本节点 revision 的原始 `raw_middle_json`，核对 `source_ref.page_idx / detailed_index / bbox_pt`；不匹配时返回 `attributes.ocr_render_warning`。缺失原始 middle 时使用原文分隔符/公式节点回退。仅增加详情响应，不重写存储节点、`text_raw`、`text_norm` 或 `char_span`，不改变合并布局及证据定位。
