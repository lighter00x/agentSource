# P5：图像理解多 Agent

> 周期：D4～D9 ｜ 负责人：BE-3（结构化 Skill、校验规则）、BE-4（FigureAgent、Coordinator、管线）
> 前置：P3 的 SkillRuntime 与 CapabilityRouter；P4 的分子链路与标注图
> 设计文档对应：6.6～6.7 测定与指标词典、7.1～7.2 管线、7.5 分类、7.6 调用纪律、7.7 多 Agent 协作、8.5～8.8 表格与图表、8.14 领域抽取、11 验证体系

---

## 1. 目标

每个视觉节点由一个 FigureAgent 负责。默认启用受控自主探索，分类和分子准备后，由模型选择工具交替读取证据、提取、识别和校验，不再强制每图只调用一次结构化。保留固定模式用于回归对照。Coordinator 负责分发、汇总和定向复核。完成后，一篇论文的所有图表都能产出带证据、经过自检的结构化结果，并能批量运行。

## 2. 范围

**做**

- 结构化 Skill（固定模式每图 1 次，探索模式受提取预算限制，按类型合并 schema）：`table_extract`、`scheme_extract`、`structures_extract`、`chart_digitize`（含 `property_plot` 合并）；`docking`、`metabolic`、`protac`、`pharmacophore`、`common` 等列为尽力完成。
- 确定性后处理：`measurement_map`、`value_parser`、指标词典（含 `unit_class` 和取值范围）、`sar_record`、图表坐标标定与 CV 提取。
- PDF 文本层 token 提取（数值自检用）。
- FigureAgent 子图：classify → plan → prepare → structure → post → L1 → L2 → remediate → verify → report。
- L1 确定性校验（结构、数值、图表结构、内容四个维度）；L2 单次四维自检（综合回绘图 + 核对清单裁剪）。
- 补救：动作白名单（本阶段不含 `run_code`，它在 P9）；由 CapabilityRouter 按失败的检查码预选动作，以 `action_enum` 形式让模型选择。
- Coordinator：公共黑板 v1/v2、按页序分发、文档级并发上限、Reconcile（冲突 → 定向问题）、`query` 模式。
- 批量管线：Celery 任务 + LangGraph 主图（Send 扇出）、断点恢复、按端点的全局限流（基础版）。
- 调用计数：每图、每篇的模型调用按 D38 统计，超出例外上限时告警。

**不做**

全文理解（P6）、`run_code` 与沙箱（P9）、负载降级（P9）、完整的模型网关（P7，本阶段用 P3 的任务映射表 + 简单信号量）。

## 3. 目录与模块

```text
drugpaper/
├── skills/builtin/
│   ├── table_extract/  scheme_extract/  structures_extract/  chart_digitize/
│   ├── measurement_map/  sar_record/  panel_split/
│   └── (尽力) docking_extract/  metabolic_extract/  protac_extract/  pharmacophore_extract/  common_extract/
├── facts/
│   ├── measurements.py  assays.py  value_parser.py  metric_dictionary.py
├── vision/
│   ├── text_layer.py             # PyMuPDF get_text("words")，按节点 bbox 取 token
│   ├── chart_cv.py               # 坐标标定、颜色掩膜、柱体/曲线提取、回绘
│   └── overlay.py                # 综合回绘图（编号、单元格值、刻度、数据点）
├── validators/figure/
│   ├── structure.py  numeric.py  chart.py  content.py      # L1 四个维度
│   └── checklist.py              # L2 核对清单生成与裁剪
├── agents/figure/
│   ├── graph.py                  # FigureAgent 子图
│   ├── state.py  report.py       # FigureAgentState、FigureReport
│   └── remediation.py            # 动作定义、执行、verify
├── agents/coordinator/
│   ├── blackboard.py  dispatch.py  reconcile.py
├── orchestration/pipeline_graph.py
└── workers/celery_app.py  tasks.py
config/metric_dictionary.yaml  ring_lexicon.yaml(占位)  routing.yaml(补全)
```

## 4. 关键设计要点

### 4.1 固定模式调用基线（探索模式不适用）

| 调用 | 任务名 | 输入 | 输出 |
|---|---|---|---|
| ① 分类 | `visual_classify` | 768 px 图 + 图注 + 代码先验 | `VisualClassification` |
| ② 结构化 | `figure_structure` | 标注图 + 检测框 + E-SMILES + 文本层 + 图注脚注 + 引用段落 + 黑板摘录 | 该类型的全部结构化结果（合并 schema） |
| ③ 自检 | `figure_self_check` | 原图 + 综合回绘图 + 核对清单 | `ReviewResult` |

例外（单独计数）：超出像素上限的大表切片；分类置信度 < 0.6 时的强模型复判；补救调用。

### 4.2 L1 校验规则

按设计文档 7.7.4 的四张表实现，每条规则一个检查码（如 `C_TEXT_LAYER_MISS`、`C_UNIT_CLASS`、`C_AXIS_RESIDUAL`、`C_UNREF_BOX`），检查码同时是补救动作的触发条件。重点：

- **文本层比对**：矢量 PDF 中，逐个核对转写出的数值是否出现在节点 bbox 内的文本层 token 中；这是对 VLM 转写最强的独立校验。
- **单位与范围**：按指标词典的 `unit_class`、`hard_range`、`soft_range` 判定。
- **回绘重合度**：数字化结果回绘成掩膜，与按图例颜色提取的原图掩膜比对。

### 4.3 L2 自检

- 一次调用覆盖四个维度；L1 告警作为重点核对项。
- 已通过文本层比对的数值不进入清单。
- 清单超出容量时按"L1 告警 → 未验证数值 → 编号 → 其他"截断，其余标 `unchecked`。
- 判定：无 `incorrect` 且 `unsure` ≤ 10% 视为通过。

### 4.4 补救

- CapabilityRouter 按失败的检查码预选 2～4 个动作（L1 卡片），模型用结构化输出选一个。
- 执行后先全量重跑 L1（零调用），再只对补救项做 VLM 核对。
- 每图最多补救 2 轮、4 次调用、180 s；超出时标记 `needs_review`。
- `apply_review_fix`、`apply_text_layer` 产生的值标注来源，优先级低于人工。

### 4.5 Coordinator

- **黑板 v1**：标题、摘要、正文编号清单（正则）、图注索引、图 → 引用段落，全部由代码生成。
- **分发**：按页码顺序派发，同页先派结构类图；文档级信号量默认 6。
- **Reconcile**：按设计文档 7.7.6 的冲突表生成定向问题，最多 1 轮、8 个问题；同一图的多个问题合并为 1 次调用。
- **黑板 v2**：汇总后的化合物清单，供定向复核使用。

### 4.6 批量管线

- 每篇文档一个 Celery 任务（`acks_late`）；内部运行 LangGraph 主图，FigureAgent 通过 Send 扇出。
- Worker 崩溃后从 Postgres checkpoint 恢复，已完成的 Skill 命中 `input_fingerprint` 缓存。
- 基础限流：按模型端点的 Redis 信号量（P7 替换为完整网关）。

## 5. 任务分解

| # | 任务 | 负责 | 估时 |
|---|---|---|---|
| 5.1 | 指标词典（含 unit_class 与范围）、`value_parser`（设计文档 8.6 的全部数值格式） | BE-3 | 0.75 天 |
| 5.2 | `table_extract`（含切片）与 `measurement_map`、`sar_record` | BE-3 | 1 天 |
| 5.3 | `scheme_extract`、`structures_extract`（迁移 prompt_v2 规则，加 status 与 evidence_text） | BE-3 | 0.75 天 |
| 5.4 | `chart_digitize`：一次结构化调用 + 坐标标定 + CV 提取 + 回绘；`property_plot` 合并 schema | BE-3 | 1 天 |
| 5.5 | PDF 文本层提取；L1 四个维度的校验规则与检查码 | BE-3 | 0.75 天 |
| 5.6 | 综合回绘图与 L2 核对清单（生成、裁剪）；自检 prompt | BE-3 | 0.5 天 |
| 5.7 | FigureAgent 子图、状态、FigureReport；调用计数 | BE-4 | 0.75 天 |
| 5.8 | 补救动作（redetect、recognize_region、rebind_labels、reslice_table、retranscribe_cells、recalibrate_axes、reclassify、split_panels、apply_review_fix、apply_text_layer、fix_unit、grep/search、get_figure_report、give_up）与 verify | BE-4 | 1 天 |
| 5.9 | Coordinator：黑板、分发、Reconcile、query 模式 | BE-4 | 0.75 天 |
| 5.10 | 主图、Celery 任务、断点恢复、基础限流 | BE-4 | 0.5 天 |
| 5.11 | 尽力项：docking、metabolic、protac、pharmacophore、common 抽取 | BE-3 | 余量 |

## 6. 测试

| 类别 | 用例 | 期望 |
|---|---|---|
| 数值解析 | `12.7 ± 2.4`、`24 × 10−6`、`79 (Kd = 1.56 × 10−4 M)`、`<0.1`、`nd`、`3.2/4.1` | 与设计文档 8.6 的解析结果一致 |
| 表格 | 样例 Table 3 | 11 项测定全部抽出并正确映射（PAMPA、PPB、hERG、CYP 等），条件来自脚注；`CYP34A` 映射为 cyp3a4 并带规范化告警 |
| 表格 | 样例 Table 1 | 10 行，母核 + 位置变量记录完整，EC50 带误差 |
| 文本层 | 样例 Table 3 | 转写数值全部在文本层中命中 |
| FigureAgent | FakeVLM：自检通过 | 恰好 3 次调用，状态 `ok` |
| FigureAgent | FakeVLM：自检报单元格错误 → `retranscribe_cells` → 通过 | 状态 `remediated`，调用数符合预算 |
| FigureAgent | FakeVLM：补救始终失败 | 2 轮后 `needs_review`，不超预算 |
| 预选 | `C_AXIS_RESIDUAL` 失败 | 候选动作只有 recalibrate_axes、reclassify、give_up 等相关项 |
| Reconcile | 两张图对 `12a` 给出不同结构 | 生成两条定向问题，分别发给两张图 |
| 管线 | 处理中途杀掉 Worker | 重启后从 checkpoint 继续，已完成的 Skill 不重复调用模型 |
| 调用纪律 | 样例论文全量运行 | 每图调用数 = 3 + 例外；每篇调用统计写入报告 |

## 7. 验收标准

- [ ] 样例论文所有图表都有 FigureReport，L1/L2 结果可查询。
- [ ] Table 3 的 11 项测定映射正确；Table 1 的 SAR 记录完整。
- [ ] 至少演示一次"L1 告警 → 补救 → 通过"和一次 Coordinator 定向复核（FakeVLM测试已通过，真实整体演示仍待验收）。
- [ ] 10 篇论文批量运行成功率 ≥ 90%；单篇调用数和耗时分解写入报告，与设计文档 7.6 的预算对比。
- [x] 接口冻结：`FigureReport`、`TableGrid`、`ChartDataset`、`Measurement`、`Assay`、检查码表、`routing.yaml`。

## 8. 风险

| 风险 | 对策 |
|---|---|
| 单次结构化调用输出过长被截断（大表） | 切片作为明确例外；输出 token 上限按任务配置；截断检测后自动切片 |
| 自检本身判断失误 | 封闭式逐项核对；补救前后都保留版本；`apply_review_fix` 的值优先级低 |
| 图表 CV 在复杂配色上失败 | 同一次结构化调用中让 VLM 给出难分系列的像素点；CV 失败的系列在补救中处理 |
| 补救比例高于预期，调用量上升 | 监控补救率，按检查码调整 L1 阈值 |

## 9. 交给下一阶段的内容

- P6：所有图表的结构化结果、测定、路线边、编号绑定，用于页包、化合物归一和路径推理。
- P8：FigureReport、AgentTrace、Coordinator 记录，用于 UI 展示和重跑。
- P9：补救动作框架（新增 `run_code`）、调用计数（负载降级依据）。

## 完成记录

2026-10-02：开发项5.1～5.11已实现并覆盖单元测试。图像Agent/补救/Coordinator/Send管线、渐进披露、PostgreSQL断点恢复、Celery硬终止恢复与Redis共享限流已加入回归；接口快照已冻结。真实10篇文献验收使用用户提供的tests/data与MinerU8002，处理状态与抽取准确性分别记录，尚未通过的标准保持未勾选。

实现/测试映射：`../codemap/phase-5-codemap.md`；接口：`../interfaces/figure-agents.md`；真实验收及最终测试证据：`../../samples/outputs/phase-5/20261002/completion.md`。


## 2026-11-22：受控 FigureAgent 探索基础实现

`structure` 仍是可重跑的调试检查点，内部由模型原生 tool calling 驱动，而不是预设关键词搜索循环。模型可以交替使用全文 `grep/search`、目录/节点/上下文/页面读取、关联图表图像查看、当前图 Skill 提取、当前图已有分子框重识别，以及分子回填/拼接和 L1 校验。实际提取继续服从对应 Skill 的 JSON Schema；Agent 无权直接改写结果或执行任意代码。

启动时提供有上限的文献地图（章节、图表标题和位置），模型自主选择深入读取范围。读取绑定用户和文献版本，关联图表只读；识别和提取只能作用于当前图，人工确认的结构不允许被自主重识别替换。无 shell、任意网络或跨文献权限。检索结果保存证据编号、工具参数、节点引用和页码，关联图像保存 blob 引用并附给解析 Skill。工具返回内容和文献文字均作为不可信数据。

默认限制：64 轮决策、80 次工具调用、3 次成功提取（最多 6 次尝试）、1800 秒总探索时间；默认最多查看 8 次图像（原图、整页与局部放大合计，由 FIGURE_EXPLORATION_MAX_VISUAL_READS 配置），文字证据总量限制 48000 字符。前四项通过 `FIGURE_EXPLORATION_MAX_TURNS/MAX_TOOL_CALLS/MAX_EXTRACTIONS/TIMEOUT_S` 配置；轮次最多可配置为 128，工具调用最多为 160。`FIGURE_EXPLORATION_ENABLED=false` 回到固定模式。超时、预算耗尽、连续失败或无有效证据时停止并保留已有结果为待复核，不保证未知信息一定能补齐。进程取消会向上传播，不吞掉取消信号。

存在未解决问题或校验错误时，必须先尝试文献/上下文/关联图像或分子取证，不能仅提取和校验就放弃。必须在新增证据后重新提取并校验，才能调用 finish；工具成功、Agent finish 和最终质量审核通过是不同状态。后续 L2 独立复核继续执行；单图失败不扩大到其他图。

工作台在结构化阶段显示 Agent 决策、工具开始/结果和停止原因，原有模型请求、响应、Skill 和图像记录继续保留。最终图表 JSON 附带 `exploration`，模型调用计数单独统计 `exploration_calls`。历史结果不会自动改写；从 structure 重跑才启用新流程。


### 2026-11-22：补充多模态上下文查看

`inspect_visual(node_id, region_index?, max_side?)` 优先按已有布局读取完整关联图表，可选跨页分段；单图信息不足时再用 `inspect_page(page, max_side?)` 查看其所在整页。普通工具不暴露或接受自定义 bbox。页面不存在可用图表节点时允许直接查看整页。仅在整页已经实际查看后才向 Agent 开放 `inspect_crop(source_evidence_id, bbox, reason, max_side?)` 作为低频兜底；必须引用那张整页的证据编号并说明具体裁剪原因，不接受凭空生成的来源。页码从 0 开始；bbox=[x0,y0,x1,y1] 归一化坐标，必须为有效正面积。图像以300dpi裁剪，长边限制1024–2400像素（默认2000），单次至多8个跨页分段，整体共用8次视觉调用预算。

图像直接进入 Agent 多模态消息，并通过带页码、页面bbox和blob引用的视觉证据进入下一次提取；prompt型和Python表格型 Skill 共用证据装配函数。新证据仍需重提取、校验才能结束。存在未解决Markush候选且文献有相关表格时，未经相关表格或其页面视觉核查不能直接 finish（达到总体预算则待复核退出）。前端工具结果可预览证据图并打开原图。

视觉优先级与版本保存至探索记录，并参与 FigureReport 缓存指纹，避免旧策略的缓存结果冒充新策略运行。bbox仍保留在底层渲染接口中供人工调试；Agent通过上述受控工具使用。

功能测试补充：发现 Markush 拼接未决时，校验结果直接给出候选关联表格，先完成相关图像取证再重提取；结束检查也先提示未满足的图像取证要求，避免先耗尽提取预算。服务失败记录为 extraction_attempts，不占用成功提取次数；工具总次数、尝试次数、轮数和总时间仍分别受限。探索结果记录 unapplied_evidence，明确哪些新增证据尚未进入提取结果。

### 2026-11-22：由粗到细的文献取证设计

本节记录设计约定，具体落地范围和验证见末尾实施记录。保留现有 FigureAgent 和结构化检查点，不另建每页 Agent。目标是在当前图信息不足时，由同一个 Agent 自主定位全文证据，逐步增加阅读精度，最后把证据用于当前图的提取、映射与校验。

#### 信息缺口与取证目标

首次查看当前图、图注和已有文本上下文后，Agent 可先提出缺口，也可先做一次提取，再根据结果及校验发现缺口。每个缺口记录 gap_id、待解决问题、受影响的结果字段路径、检索线索、证据引用、状态和未解决原因。示例：`1–10 各编号对应的取代位置未知`；`Alk 的具体含义尚无证据`；`箭头条件代号 a 的定义未找到`。状态为 open、resolved、conflict 或 unresolved；不是只记录一个无法定位的总置信度。

Agent 工具调用附简短目的和 gap_id。校验器提出的问题同样进入缺口列表；模型也能自主提出结构化校验发现不了的语义问题。工具使用由模型决定，以下阅读层级是优先级与升级条件，不是每张图必须执行的流水线。

#### 阅读层级与升级规则

| 层级 | 输入与动作 | 升级条件 |
| --- | --- | --- |
| 当前图 | 原图、SOM、图注、已有邻近正文；列出具体缺口 | 当前材料无法回答 |
| 全文定位 | 使用章节和图表地图、全文 grep/search，按图表编号、化合物编号、缩写、条件代号定位；读取命中附近文本 | 证据依赖表格布局、结构位置，或文字未能定位 |
| 多页概览 | 对候选页分批提供低分辨率整页预览、页码、图表节点及短摘要；Agent 选择需要细读的页面/节点 | 找到可能承载答案的图表或正文页 |
| 图表细读 | 优先 inspect_visual 读取已存在的完整图表；需要周边文字或布局再 inspect_page；正文页可直接整页读取 | 明确细节仍不清楚 |
| 局部核对 | 优先已有布局块或表格行带；必要时引用已查看整页证据进行 inspect_crop | 仍不足则转向其他来源或保留未决，不反复盲裁 |
| 证据应用 | 新证据进入当前图 Skill，更新受影响字段，执行回填、拼接和校验 | 有新的实质缺口且预算允许时继续探索 |

已找到明确关联节点时直接细读，不必先生成预览。没有关键词线索时允许从多页概览开始；预览不足时可翻查下一批，不能因正文搜索无命中就认定文献没有答案。图表跨页时按一个逻辑节点提供所有片段及 continuation 关系，必要时分批加载，不拆成独立任务。

#### 全文地图与多页概览工具

扩展现有文献地图：按页列出 PDF 页索引、展示页码、可识别的印刷页码、所属章节、图表节点编号、图注摘要和文本是否可用。地图本身分页/按章节获取，避免长文献启动时超过现有 100 节点截断后不可发现。

拟新增 `preview_pages(pages, gap_ids, reason)`：单批默认 4 页、最多 6 页，每页独立图像与独立标识，预览长边建议 768–1024 像素；结果包含 preview evidence_id、页码、图表 node_id、跨页关联、短文本摘要及图像引用。参数必须来自当前文献地图，去重并校验文献版本与页范围。模型根据预览返回的真实 node_id/page 调用现有高清工具；不要求它从缩略图猜 bbox。

有明确线索时，候选页优先包含显式图表引用、编号/关键词命中和所在章节，必要时补相邻页。没有线索时，Agent 可分批浏览未查看章节/页面；候选排序应兼顾覆盖范围，不能永远只展示相邻页或同一组高分命中。记录已经浏览和排除的页面及简短理由，避免重复加载。

预览只用于选择阅读位置；小字、分子结构、表格数值和取代基位置必须以高清证据核对。概览和细读是两种操作：允许先查看页面预览，再优先选择该页已有图表节点细读，保持节点优先、整页补充、裁剪兜底的原则。

#### 证据应用与化学约束

每条证据保存文献版本、来源节点/页、图像或文本引用、阅读精度、相关 gap_id、直接观察到的事实与 Agent 的解释。来源文字和图像是数据，不能改变工具权限或系统规则。有关联不等于已证实；冲突证据并列保留，不能默默用后一次输出覆盖前一次。

每个缺口只有在证据进入提取、相应字段实际更新且校验通过后才能 resolved。工具已返回、页面已看过、JSON 合法均不能单独作为解决依据。保存 extraction_version 与 applied_evidence_ids；结束前检查是否存在相关但尚未应用的高清证据。

例如 Figure 2 的 `1–10`：先定位 Table 1，再高清读取各编号行及必要脚注，把“编号→醛基位置/取代位置/X/Y/Z”作为待核对的映射，重提取当前图。只有实际输出对应编号记录后才解决此缺口；6 种通式候选不能冒充论文的 10 个编号。对 Alk，找到个别 Me/Et 实例只能支持这些编号实例，不能据此把所有 Alk 的定义缩窄为 Me/Et。保留既有约束：反应物 Markush 不枚举，产物根据明确证据展开与拼接；来源图表只读，不把来源图的分子 index 当成当前图的 mol_id，引用必须带来源节点。

#### 上下文与预算

证据仓库保存全部原始材料，当前模型消息仅保留任务、当前图、未决问题、相关摘要和正在使用的高清证据。预览完成选页后，旧预览可退出下一轮请求，但其引用与选页记录保留并可重新加载；被替换的历史工具结果保留摘要和证据编号。整理模型消息时保持 tool call/result 配对和模型所需的响应历史合法，不直接删除配对中的一半。

调度层新增按实际图像数量、分辨率和请求大小计算的预算，不能继续只计算工具调用次数。建议初始预览每批 4 页、最多 2 批；它们也计入现有总轮数、工具次数、视觉调用次数和总时长。单次请求活动图像数先以 12 张作为可配置的试验上限，包含目标原图/SOM、预览和高清图；这不是上游接口已验证的最大值，运行前需结合模型能力及请求大小校验。目标跨页片段占用预算时自动缩小预览批次或分批细读，不能截断逻辑图表后假装完整。

保留现有默认 8 次视觉读取、3 次成功提取、12 轮/24 次工具/900 秒上限；预算不足时少发预览、直接细读已有明确目标，优先为一次证据应用与校验留出时间和调用额度。模型结构化提取只附与当前缺口相关、已选定的高清证据和可追溯文本，不自动附全部预览。连续无新增证据、来源冲突无法解决、源页缺失或预算耗尽时返回 needs_review，保留已完成部分及明确停止原因。单图问题不阻塞其他图。

#### 开发调试与验收

前端在现有 Agent 探索区域提供可折叠的“问题与证据”视图：展示缺口、候选页预览、Agent 选择的页/节点及简短目的、高清取证、受影响字段的前后 JSON 对比、证据是否已应用、剩余预算及停止原因；不再为每个阅读层级新增侧边栏目。不展示模型隐含思维过程。所有证据、选择和排除记录在当前文献/图表范围内保存，可从结构化检查点重跑。

验收应覆盖：直接命中表格时跳过预览；文字无命中仍能通过概览定位页面；相关证据远离相邻页；长文献地图分页；跨页图表完整读取；预览模糊触发高清；缺口在看到表格但未更新编号映射时保持 open；冲突证据保留；旧预览退出请求后原始证据仍可追溯；图像总量/请求大小受限；耗尽预算时保留部分结果。首个真实用例继续使用 Figure 2 与 Table 1/2，分别验证编号映射和 Alk 边界，不把通式候选数量当作映射成功。

#### 基础版本实施记录

已新增 `get_document_map`（按页分页）、`preview_pages`（默认每批4页/最多2批）、`update_gap`、`select_context` 工具。预览按96dpi渲染并限制长边1024像素，每张图前附页码标识；结果包含该页已有图表节点、跨页区域和关联，后续沿用节点→整页→裁剪的高清读取策略。候选页由 Agent 根据地图和搜索自主选择，没有额外的固定关键词循环；显式关联节点可直接读取。

图像上下文默认上限12张、序列化请求大小上限24000000字节，分别通过 `FIGURE_EXPLORATION_MAX_CONTEXT_IMAGES/MAX_REQUEST_BYTES` 配置；预览批量与次数通过 `FIGURE_EXPLORATION_PREVIEW_BATCH_SIZE/MAX_PREVIEW_BATCHES` 配置。历史请求释放旧图像时保留工具调用/返回配对及 Responses 推理历史；原图不静默截断。超过可压缩范围时记录 `context_budget`，保留已有提取为待复核。预览保留在探索证据中，但不传入结构化 Skill，不计为高清核查，也不能作为关闭缺口的证据；高清证据可通过 select_context 选择与重新加载。Skill 请求同样检查图像数和大小，超限返回明确的重新选证据提示。

每次成功提取记录 extraction_version 与 applied_evidence_ids，结束记录 unapplied_evidence。缺口关闭要求先注册问题及 JSON pointer、相关证据已用于本次提取、影响字段发生变化且当前校验通过。重提取后已解决缺口重新打开等待确认；字段变化是机械检查，不代替语义准确性判断和后续独立审核。未关闭的问题自动进入最终 unresolved，不能把 Agent finish 当作全部问题解决。

前端现有结构化阶段增加“问题与证据”汇总，显示缺口状态、影响字段、证据编号、前后 JSON 变化、已应用/未应用证据以及上下文与剩余调用预算；多页预览按PDF页码独立标识，原始事件继续可展开查看，没有增加侧边栏目。

本地真实PDF验证使用同一示例论文的第3/4/6/8页预览和Table 1/2高清图，确认图像实际生成、来源可追溯，且在3张活动图像的压力预算下释放4张旧预览并保留目标及两张高清图。记录在 `samples/outputs/figure2-coarse-to-fine-local-validation.json`，没有调用外部模型或改写已有解析结果。模型是否主动选对证据、逐编号映射是否正确仍需要真实模型重跑验证；本次本地验证不宣称已经解决 Figure 2 的化学提取准确性问题。

验收记录：59项后端本地测试通过（报告 `samples/outputs/figure-context-tests.xml`），覆盖新工具、上下文释放、缺口关闭约束、固定解析、表格切片和工作台重跑；前端构建与2项现有单元测试通过，新增浏览器测试验证缺口/JSON对比/证据应用/剩余预算及相同图像引用的独立页码展示。前后端已在原隔离开发库重启，启动不执行模型；旧运行不会被改写，新运行从结构化提取检查点使用新策略。

真实后续重跑 `wd_01M80WW2PP6F89HYJQ6MPWES2A`：复用分类至编号图，从structure至report全部完成，最终needs_review。GPT请求均为gpt-6-luna/medium；Agent直接读取当前图与Table 2，并通过select_context应用证据，没有调用多页预览。3次提取尝试中2次成功，一次因Markush选项/赋值不一致触发SchemaViolation；最终有46个通式候选，38个拼接完成、8个因未限定Alk待复核，未生成具体编号variants。探索达到12轮上限；服务可用与流程完成不代表编号映射已通过验收。独立结果和验证记录见 `samples/outputs/figure2-coarse-agent-final-result.json`、`samples/outputs/figure2-coarse-agent-rerun-validation.json`。

此次发现缺口工具只接受JSON pointer，而模型沿用校验器的具体JSONPath，导致一次登记失败。已兼容 `$.pathways[0].steps[2]` 等具体字段/索引路径并统一转为 `/pathways/0/steps/2`；不支持通配符、表达式或求值。增加回归后上下文测试17项通过，策略版本提升为coarse-to-fine/3.1。该修复供后续新运行使用，不改写上述运行快照或其错误记录。

### 当前图结构来源边界修订

所有FigureAgent的结构基础固定为当前图已检测并识别的节点。关联页面和图表仅提供编号、取代基赋值、缩写含义、位置、条件和关系参考；不要求参考图中另画完整分子，不从其识别结果补入完整结构。新增structure_inventory仅披露当前图框ID、识别有效性及连接点标签，不向视觉模型披露SMILES/分子式。复合标签XCHYZ与独立R/X/Y按实际识别连接点分别赋值。

scheme_extract升级0.9.3，明确当前图通式局部候选与参考资料证实的具体编号variants分开：例如SOAlk留在图示通式选项中，参考行证实的SOEt进入对应产物variant；不将SOEt写入仅声明SOAlk的通式assignments，也不以一个实例定义全部Alk。反应物只保留通式，未在该端点实例化的原因必须写成输出策略限制，不能声称全文缺少资料。产物partial但variants为空会被语义校验拒绝。

mol_backfill升级0.5.1，FigureAgent强制传structure_source_scope=current_figure：禁用按编号跨图查完整结构的自动兜底，缺少本图来源时保留current_figure_structure_missing错误。独立调用者原document模式保留，不作为FigureAgent绕过边界的工具。具体编号实例的纯文字赋值可使用与通式候选相同的显式原子连接算法；母核、整数片段来源仍必须通过本图mol_id绑定，拼接结果继续检查图合法、唯一、连通且无未解占位符。

探索默认轮数由12提升到20，为取证、登记、重提取和校验留出空间；24次工具、900秒及成功提取上限保持。相关125项本地回归通过，真实重跑沿用原分子准备结果从structure开始，结果另存，不覆盖旧运行。

真实验证 `wd_01M80YA1XA10B4AEK2JHKE31BC`：structure→post→L1→L2→report全部完成，GPT请求均为medium。读取当前Figure 2与Table 2后，产物16–27、45–47系列保存15条variants，其中16–27及45共13个成功拼接；所有成功实例的结构绑定只来自本图p002.b000.m003，没有从Table 2导入完整结构，26号拼接结构与当前图单独识别的26号一致。46/47带位置的Cl赋值未被当作普通Cl强接，保留未完成；其中47的位置文字本轮存在冲突，需复核。1–10逐编号赋值和未限定Alk的通式候选仍未解决，最终needs_review，不能声称整张图提取正确。结果在 `samples/outputs/figure2-source-bound-final-result.json`，对比与来源核验在 `samples/outputs/figure2-source-bound-rerun-validation.json`。

额外核查发现Agent旧material投影会携带当前图识别化学串；已移除该字段，仅保留连接点摘要，针对性回归通过，策略版本current-figure-structure/4.1。此项加固供随后新运行使用，不改写上述运行的4版快照或其原始输入证据。

### 单流程优化路线Skill与逐字段取证（2026-11-22）

scheme_extract升级0.10.0，保留现有pathways/steps输出和当前图结构边界。规则围绕通用对象组织：真实箭头与分支/汇聚、优化/筛选/反应的语义区分、重复中间体身份、条件代号溯源、产物系列成员覆盖、连接位置与立体限制。删除重复的特定论文JSON示例，保留一个可经公开Schema及语义模型校验的通用示例；不把某篇编号或某个开放缩写写成固定任务。

新增根information_gaps（可选，旧结果兼容，Skill新输出要求显式提供）。每个缺口有稳定gap_id、question、具体业务JSON pointer字段路径、category、next_action、query_terms、source_hints和实际evidence_ids。类别区分缺证据、证据冲突和结构表示不支持。反应物仅保留通式属于输出策略，不生成额外查找任务；没有条件标注也无代号的null不自动构成缺口。开放符号、组合候选、逐编号赋值和拼接成功分别判断，不能互相替代。

FigureAgent在每次提取后自动登记Skill缺口，使用skill:前缀归入当前图独立日志，无需模型主动调用登记工具。缺口从新提取中消失不会自动关闭。finish要求各个Skill缺口分别关闭，或以unresolved/conflict关联已应用的实际文字/高清证据和原因；一个缺口查过表格不能替其他缺口完成处置。检索无命中的证据只表示该次查询无结果，不表示全文不存在。已明确的结构表示能力限制允许注明原因交复核，避免无效循环。预算达到上限仍可退出并保留needs_review，不强制无限探索。

关闭一个Skill缺口不再要求其他无关字段全部无错：证据必须实际应用、受影响字段必须存在、当前Skill不能仍报告同一缺口、对应字段不能有校验错误；无法定位字段的错误保守阻止关闭。其他来源的手工缺口沿用原关闭约束。这里只验证流程和字段约束，不代替语义正确性审查。策略版本current-figure-structure/4.2，旧运行与输出文件保持原样。

此改动不声称已为所有图类自动生成信息缺口：协议与Agent接收逻辑可复用，本次完善并启用的是单流程优化路线Skill。真实模型能否稳定报告所有缺口仍需后续样例验证。

本轮验证：第一组120项本地回归通过（Skill缺口、上下文、系列Schema、探索调度、具体实例及候选拼接）；后续53项兼容检查通过（含重复的缺口/上下文检查及工作台、检查点复用、Agent集成）。包加载、公开Schema与语义模型一致性、通用JSON示例及Ruff检查通过。本轮未调用外部模型，未重跑或改写既有Figure 2真实解析结果，不能据此宣称真实提取准确率已达标。

真实重跑验证（2026-11-22）：运行wd_01M811Y6YNNXTVK8B15EW0S12F从wd_01M80YA1XA10B4AEK2JHKE31BC复用分类至编号图，加载scheme_extract 0.10.0及current-figure-structure/4.2。structure→post→L1→L2→report均完成，最终needs_review，无任务异常。GPT请求均为gpt-6-luna/medium；结构化及探索约324秒，独立视觉复核约74秒。

Agent本轮对两组成员分别grep并读取Table 1/Table 2节点高清图，将证据应用于第二次提取。保存25条具体编号variants（此前15条），13个拼接完成（16–27及45），与此前成功数一致；当前图已有母核仍是唯一结构来源。1–10共10条待复核：OCH(H)F/OCH(F)F等表达未被拼接器接受，且实际表格的CHO/XCHYZ位置差异尚未逐成员结构化编码。46/47仍受具体位置表达限制。第二次输出对1–10误用反应物not_expanded，另一系列把多个基团合到同一case且改变图示选项，导致2项markush_candidate_enumeration_failed；本轮未生成上一轮46个通式候选，属于输出完整性退化，不可称为整体解析成功。

自动缺口登记生效，2次过早finish被逐项约束拒绝后Agent补充各项处置；另一次update_gap因修改已有问题和字段路径被拒绝，随后恢复原身份成功记录。独立视觉复核将C_BACKFILL_ERR评为correct，但确定性回填仍有12项实例失败及2项候选枚举失败，最终未据此提升通过状态。结果单独保存samples/outputs/figure2-skill-v010-final-result.json；对比、工具错误、来源核验和限制在samples/outputs/figure2-skill-v010-rerun-validation.json。此轮验证证明补充查找与逐缺口结束约束有效，不能证明位置建模或通式候选输出已完善。

### FigureAgent可调用拼接与修复工具

新增agents/figure/assembly_tools.py，提供inspect_assembly、try_assembly、apply_assembly。当前图既有实例路径/母核/基团ID是唯一结构输入；赋值和位置语义改变必须有已应用的文字/高清证据及说明。每批最多8个、每图32次试拼，复用Agent全局预算；成功尝试经过原字段和识别来源指纹检查才允许应用，之后使校验过期、必须重做validate_result。原生集成测试验证试拼不修改、应用后直接finish被拒绝、再次校验后才可结束。

新增chem/assembly.py供试拼与最终实例回填共享，连接点明确时沿用原子拼接；补齐已有位置适配器的占位符移除/标签重映射。只移除指定末端dummy，保留真实母核原子；不满足已支持骨架和唯一产物检查则失败。旧通用E-SMILES/检测片段拼接路径保留。基团等价规范化只在工具候选中显式执行，不在最终回填中盲目自动替换全部实例；已知位置表示缺口不能靠修正简称绕过。

对上一轮12项失败实例进行了本地工具验证，位置参数由开发者根据已授权Table 1图像录入：12/12试拼成功，应用后25条编号实例均通过结构拼接计算；第一系列得到10个不同结构，3号与当前图单独识别结构一致。46/47使用上一轮位置取值验证工具路由，没有新增独立位置阅读认证。未执行新一轮自主模型探索，也未修复通式候选展开错误，整体仍needs_review，不能视为完整解析验收。验证记录在samples/outputs/figure2-assembly-tools-local-validation.json，预览在samples/outputs/figure2-assembly-tools-preview.json，旧正式运行结果不覆盖。

工具实现验收：111项本地测试通过，覆盖工具调用、批量原子应用、试拼不改原文、来源限制、过期结果、位置处理与原有回填/工作台；此前探索与检查点兼容组50项通过（部分用例重复）。Ruff与9个相关Skill包加载通过。服务已重启加载策略4.3，启动未发起新的模型解析。

### 自主Agent后续全流程验证（2026-11-22）

按用户要求运行wd_01M815EVA25M6Z4GNP1G4PS88P，source_run=wd_01M811Y6YNNXTVK8B15EW0S12F，从structure开始。classify/plan/detect/recognize/som均复用，事件中无新的检测识别调用。快照为scheme_extract 0.10.1、策略4.3，GPT均medium。structure→post→L1→L2→report完成，无任务异常，最终needs_review。

Agent自主检索正文、读取Table 1/Table 2高清图并重提取。第二次提取因Markush选项不一致及不完整position_definitions触发自动Schema修复；修复输出删除了1–10全部position_definitions。最终后端虽然标记24/25编号实例拼接completed，但1–10只得到4个不同结构，遗漏逐成员位置差异，不可将24解释为正确提取数量。与上次基于Table 1明确位置的本地预览比较，仅4、8号结构一致；该预览也不构成新的独立化学真值认证。

本轮inspect_assembly/try_assembly/apply_assembly调用次数均为0。遇到错误后Agent直接选择待复核结束，未使用新工具修复；缺口结束约束曾拒绝一次过早finish，随后补登记。47本轮读取为2′-Cl（上一轮为2-Cl），触发occupied_positional_site；8个Alk候选继续未解，另一个3→3 variant link被隔离。通式候选44个，其中36个计算completed、8个待复核，数量不能代替候选语义正确性。

正式输出单独保存samples/outputs/figure2-assembly-agent-final-result.json；独立核查在samples/outputs/figure2-assembly-agent-rerun-validation.json，quality_audit_status=failed_position_preservation_and_autonomous_assembly_remediation。本轮只验证实际自主行为，没有人为补位置或调用试拼工具改写结果。后续需防止Schema修复通过删掉必要信息过关、为位置依赖建立回填前置约束，并完善可修复错误到工具调用的调度。

### 通用跨图成员完善机制（2026-11-22，5.0）

用户澄清要求为通用Agent/Skill能力，不是指定某张图作为固定检索目标。新增reference_assignments.py实现候选发现、相关性/逐成员出处登记、重提取字段保留及回填约束。没有写死文献编号或来源图名。不同来源可补充当前图没表达的取代、位置等维度，原图通式选项不被补充资料改写；结构母核仍绑定当前图，其他图的检测整数不冒充当前mol_id。

关键约束：Schema修复不能通过删除已经登记的字段改变事实；缺失字段可按已确认记录补齐，矛盾不得静默覆盖。来源缺失、目标改变、赋值不一致等在分子组装及结构事实持久化前阻断相关实例，不是事后仅修改展示状态。没有逐成员证明的重复模板赋值会触发系列身份未建立，推动重新取证，而非把复制骨架算成多个成功化合物。相同结构确有出处时可登记对应，避免把“不同编号必定结构不同”作为化学断言。

position_definitions保留开放的原文维度，既有特定化学适配器的限制不再成为全局数据Schema限制；不支持的内容保留并复核。原生try_assembly检查已登记的来源约束，失败结果不能通过apply工具绕过；finish遇到未尝试修复的实例失败也会拒绝结束。默认32轮、40次工具，预算仍有界。

通用测试使用不同编号与图名，覆盖候选定位、真实高清来源、误关联/跨文献拒绝、原图与补充信息分离、Schema输出删除字段后的恢复、冲突阻断、事实计算前拦截、纠正后解除阻断、未知维度保留以及原生发现→查看→登记→重提取→校验。历史真实运行不改写，本次实现的独立模型验证需使用新的运行快照；不得用本地回归通过宣称任意论文已正确解析。

本轮验证：最终75项兼容测试通过（含通用关联来源、原生工具调度、字段保留、阻断与检查点）；此前两组121项、87项本地检查也通过，存在重复用例，不作累加统计。相关Skill包/工具Schema加载、Ruff检查通过。本轮未执行新的真实模型解析，没有把某个固定图名写入Agent指令或通用规则；真实模型自主取证与化学内容正确性仍需在新运行快照中验证。


### 结构化状态修复与内容保留（2026-11-22）

真实运行wd_01M818C5THBNHB6TVSVJHSDD8P在第三次提取时，模型先返回完整pathways，但根status=ok与未解compound_series不一致。网关要求重写JSON后，模型只修状态并将pathways清空，空数组通过包Schema；后续引用约束回填遍历已丢失的路径，触发IndexError。该运行失败，未产生可用最终结果，诊断见samples/outputs/figure2-reference-agent-rerun-validation.json。

scheme_extract 0.11.1新增保守状态修复：仅当语义校验全部错误都是总状态与未解成员/缺口不一致时，完整复制原始内容，将ok降为unclear，记录validation_adjustments并重新严格校验；其他字段/化学错误仍失败，不能降级掩盖。避免为已知的派生状态错误重新生成整份JSON。

Agent重提取采用事务保护：已有非空pathways、optimization_steps、rows或compound_instances变为空时拒绝替换，恢复原提取及引用证据应用状态，向Agent报告extraction_content_lost，失败计入尝试预算。该保护不是化学真值认证，不自动补造缺失内容。引用回填遇到已消失目标保留reference_target_changed与needs_review，跳过不存在的路径，避免越界崩溃。

本地87项回归通过，包含实际失败响应完整性复现、单请求状态修复、其他语义错误保持失败、空提取回滚、跨图来源约束和上游复用安全。真实模型重试使用新快照，结果另记。


### 前置独立结构审查与局部补丁（2026-11-22，策略6.0）

用户进一步明确：先把合成路线图的结构及当前图声明的全部产物通式可能情况找全，再执行确定性回填/拼接。提取后发现问题，不全量重新生成JSON。scheme_extract升级0.12.0；新增structure_audit 0.1.0。

实际流程为：复用检测/识别/编号图 → 首次ID-only提取 → 独立结构审查 → FigureAgent按需查阅图文 → 对错误字段或缺项应用局部补丁 → 独立复审 → 固定函数回填/拼接。实际组装失败后才由Agent介入补证或修正。反应物继续只保留通式，产物展开当前图声明的可能情况；这些可能情况与参考资料确认的具体编号成员分离。当前图没有逐编号对应时保留未知，不能要求审查者编造。

结构化阶段工具边界（2026-11-25）：`inspect_assembly`、`try_assembly`、`apply_assembly`、`recognize_molecule` 在工具列表和执行端均受阶段约束。当前结构审查通过且 `validate_result` 对当前版本返回实际 `backfill_errors` 后才开放分子恢复；修改结果使校验过期，校验成功或尚未校验时不开放。模型材料、恢复记录、结构读取和工具返回使用 ID-only 视图，过滤识别 SMILES、计算分子式、回填结构及识别连接标签，保留原文和图示变量赋值；原始运行记录不改写。结构化审查只依据图像及占位编号，不以识别标签约束图中变量。

审查由独立模型请求执行，不共享FigureAgent历史；只接收当前图原图、已有编号图、ID-only结果、当前图连接点摘要及相应提取规则，不传关联页图像或识别SMILES。figure_structure_review是独立可配置任务，当前gpt-6-luna/medium。四项审查覆盖topology、molecule_placeholders、markush_completeness、source_scope；逐问题提供实际JSON pointer、可见依据和建议。审查绑定精确提取哈希；任一补丁或组装赋值修正后旧审查失效。

新增原生review_structure、patch_structure。局部补丁使用expected_version、operations(add/replace/remove)、evidence_ids、issue_ids及note；最多20操作/32KB，每图最多5批补丁、6次成功审查，仍受总工具和时间预算约束。数组追加用/-；已有对象/数组不得整体覆盖，删除数组项需对应审查问题。校验原包Schema、语义、当前图分子ID、原文来源约束；所有操作通过后一次提交，否则完全回滚。保留修改前后字段、哈希和出处。当前图markush修改须引用当前图高清证据，其他图只可补充有依据的variants等内容。

record_reference_assignments对合成路线直接局部补齐已经确认的成员字段并复审，不调用全量提取；原图声明选项不被改写。已有初始提取后，再调用extract_figure会被拒绝并指向局部工具。后处理不再修改原始ID-only提取，仅在backfilled_extraction输出计算结果。

未通过精确版本的前置审查时，不调用mol_backfill、化学组装或路线事实持久化；保留ID-only调试结果及structure_review_blocked，最终待复核。关闭探索的固定模式也执行独立审查，但不会越过失败门禁。旧的后处理整份重写补救不会覆盖新流程的结构；模型/预算失败保留原因，不假装已成功。

前端仍使用同一个结构化阶段，名称为“结构化提取与审查”；独立审查事件有明确状态标签，原有JSON编辑器可查看问题、工具输入输出和局部修改差异，不增加冗余阶段。

实现检查：136项兼容测试通过，另有独立参考图像隔离测试通过；增加固定模式检查后的28项测试通过（测试有重叠，不累加）。前端生产构建通过。真实样例新运行wd_01M81AQFTWYECZDK0D0XCAHEZJ，从structure开始，检测识别阶段全部复用；实际模型审查及补丁效果待本轮完成后单独记录。


6.0真实样例wd_01M81AQFTWYECZDK0D0XCAHEZJ完成到report，最终needs_review，无任务异常。仅一次extract_figure及一次patch_structure：独立审查确实发现产物通式未展开，Agent只追加对应字段并复审，未整份重生成。但复审错误接受了复合连接点的分散变量列表，不能将其当作完整复合基团。独立结果及核查分别保存在samples/outputs/figure2-pre-review-agent-final-result.json和figure2-pre-review-agent-rerun-validation.json；核查明确保留该漏检。

据此补强策略6.1、structure_audit 0.1.1：从当前图识别库存的真实连接点标签，通用地检查同一连接点包含多个声明变量时是否存在联合赋值。只有单独变量列表而无任何完整联合赋值时，即便VLM给出pass也改为needs_revision并反馈字段问题。该检查不推导化学等价、笛卡尔成员数量或隐含约束，全部可能情况仍需视觉审查核对；不会用固定论文编号或图名。审查提示明确禁止独立选项冒充复合展开，保留显式氢与可见限制。17项针对性测试通过。新快照wd_01M81B43MFD0AKAHE5AS72FGC6从structure重试，沿用检测识别结果，完成情况另记。


### 沿用结构继续修正与受控通式工具（2026-11-22，策略6.3）

工作台新增“沿用结果继续审查与局部修正”。启动参数为start=structure、reuse_structure=true及source_run，要求来源有兼容的scheme_extract提取结果。检测、识别、编号图及已有JSON直接沿用，首先独立审查，然后仅对问题字段修正；不执行首次全量提取。旧快照不可变，新运行保存自己的审查、差异和结果文件。模型/上游材料的复用检查仍保留，不因沿用结构绕过。

structure_audit 0.1.3区分复合连接点与独立连接点：复合点需完整联合赋值，独立R/X/Y位置分别列出局部情况，由固定函数组合，不制造不存在的联合连接点。具有有效mol_id且没有变量的具体分子无需markush。宿主补查实际连接点匹配、有限选项覆盖、未替换变量及简单基团显式氢守恒；它们不等于完整化学真值验证。未修改的精确结构版本复用已有审查，避免反复审查耗尽预算；内容变化必需重新审查。

新增repair_markush_cases(product_path, expected_version, evidence_ids, note)，限定产物端点。工具仅利用当前结构的variable_options与识别库存中的attachment_labels，生成独立可见选项的局部候选；按完整局部group_text去重并保留等价assignments。简单单碳基团只合并已存在的原子计数，复杂缩写保留原文字供复审，不猜Alk。工具只修改选中markush的substitution_cases与expansion_status，保留其他步骤、成员、编号和母核；通过包Schema/语义验证后原子提交，保留前后值、来源、哈希和版本，然后自动独立复审。最多每连接点256次赋值、128个局部候选，沿用5批补丁及总运行预算。存在耦合限制或证据不全时，不应把独立枚举声明为实验成员列表。

reactants继续保留通式；products的可见候选、其他图表补充的有依据编号成员分别存储。repair_markush_cases不分配论文编号、不拼接完整分子、不重新生成整个JSON。真正回填/化学拼接仍在审查通过后由固定函数执行，计算失败才让Agent调用取证与试拼工具。审查失败保留ID-only调试结果，最终needs_review，不包装成可用分子结构。


策略6.3真实验证：运行wd_01M81KZT812VG7Y0NPAC5ERK6T沿用wd_01M81KCR0MG4KQZ41X53TJ9DVD已有结构，检测/识别/编号图全部复用，scheme_extract调用为0。FigureAgent自主调用repair_markush_cases修正第一产物通式，再用patch_structure删除另一产物错误的联合连接点case；只有三个目标Markush字段变化，其他JSON内容与来源快照完全一致。三轮独立审查最终passed，全部mol_backfill调用均在通过之后；所有GPT调用使用medium推理。前端入口可直接继续同类调试。

最终needs_review，无运行异常。第一通式6个局部候选、8种符号赋值，6个完成拼接；第二通式40个候选，32个完成、8个因unbounded_Alk_requires_explicit_definition保留待复核，总计46候选/38完成。Agent在实际错误后调用inspect_assembly核查支持范围，保留未定义Alk，没有猜取代基。此次数量是可见独立选项的未编号候选，不是论文已合成化合物计数；未补齐1–10或16–27、45–47逐编号对应，不能据此宣称全部分子已正确解析。最终结构化审查通过也不等于全部化学事实验证通过。

独立结果：samples/outputs/figure2-symbolic-repair-agent-final-result.json。验证记录：samples/outputs/figure2-symbolic-repair-agent-rerun-validation.json，包含完整审查问题、局部补丁、前后差异、工具序列、模型推理设置及门控顺序检查。回归67项通过，新增原生工具调度用例随11项工具测试通过（有重叠，不累加）；Ruff检查通过。前后端保持运行，局域网http://10.154.24.41:5173/。


### 逐问题全文取证与结束门控（2026-11-22，策略6.4）

scheme_extract升级0.12.1。新增get_investigation_plan原生工具，初始提取、沿用已有结构和validate_result均返回当前取证计划。计划依据当前端点成员集合、缺口查询词以及已经实际读取的正文中的Figure/Scheme/Table引用，在本篇真实IR节点中定位候选；不从请求reason或查询字符串伪造引用，不生成不存在的页码或跨文献节点。每问题最多列3个优先候选，原图本身排除。候选导航不等于证据正确，依然由Agent实际高清查看、确认相关性。

finish对每个未解问题检查对应候选来源的实际查阅情况；有多个候选时至少查看其中2个，不足2个则查看已有的。只有当前图、旧正文引用、预览或裁剪均不能冒充相关节点高清查阅；完整inspect_page可覆盖其包含的真实图表。此门控是最低查阅要求，不证明已经穷尽全文或找到了答案；新引用/更多信息仍应继续追踪，调用和时间预算保持有界。没有候选时仍由Agent使用全文搜索/地图/预览，不能把无命中当成全文不存在。

实际拼接错误中的缺定义/缺信息/未解简称按端点和原因去重生成额外取证问题，包括未定义开放变量；inspect_assembly仅说明当前输入能否计算，不能替代查阅变量的具体定义。规则明确区分missing_evidence与具体赋值已知但函数不支持的unsupported_representation。独立结构审查passed不解除全文取证义务。

确认部分成员后立即用record_reference_assignments分批登记、局部应用、独立复审并校验，不等待整个系列完整，不清空已有成员；未知保留并继续查阅。反应物只保留通式，原图markush选项和跨图证实的variants仍分别存储，本图母核/检测编号边界不变，不允许通过补充图替换当前母核或伪造SMILES。无全量重提取，无固定论文编号/图名/页码导航规则。

真实旧运行回放计划保存在samples/outputs/figure2-evidence-followup-plan.json。它自动定位到相关来源并标出尚未完成的查阅，旧运行提前finish会被新门控拒绝。真实模型续跑以新快照执行，不修改历史结果。


策略6.4真实续跑完成：wd_01M81NDAJZ3R9N7PVEBSD1Z9FT，沿用结构，检测/识别/编号图均reused，scheme_extract调用0。Agent实际调用get_investigation_plan，并自主高清查看5个相关节点（Scheme 1、Table 1、Scheme 2、Scheme 3、Table 2）；这组名称来自文献内容及导航结果，不是Agent提示或导航函数的固定目标。两次record_reference_assignments分别登记10和15个成员，保存出处并局部补充原JSON，补充后独立审查passed。所有GPT调用为medium。

人工按原PDF核查的相关成员R值，与本次Agent登记完全一致：23 SOMe、24 SOEt、25 SO(NH)Et、26/45/46/47 SO(NH)Me。跨页查阅与取代基取证已生效，不再把这些具体成员的Alk全部当作无法确认。

整体仍needs_review，无运行异常：25个编号实例中15个拼接完成，第一系列10个出现assignment_variable_absent_from_template；Agent检查能力并做了10次定点试拼，不能把来源赋值登记成功当作完整化学结果正确。原图开放Alk候选仍保留独立的不确定性，不通过已知具体成员反向限定整个通式。旧information_gaps关闭还受“原提取仍报告缺口”约束；实际本轮两次update_gap被拒绝，已补充的成员与来源没有丢失，剩余问题保留。后续应分别完善字段适配与缺口关闭流程，不将其误记为跨页工具未读到信息。

独立JSON：samples/outputs/figure2-evidence-followup-agent-final-result.json；核查报告：samples/outputs/figure2-evidence-followup-agent-rerun-validation.json，包含实际查看节点、两批登记、逐编号结果、模型设置、上游复用及人工核查值比对。98项回归通过，Ruff检查及格式检查通过，scheme_extract 0.12.1可正常加载。前后端保持运行。


### 原文赋值与拼接参数分离（2026-11-22，策略6.5）

molecular_contract=15，scheme_extract 0.12.2、mol_backfill 0.6.1。新增chem/assembly_inputs.py共享投影层：不修改原始substituents、position_definitions或参考记录，计算前依据可信母核实际连接点生成独立参数。复合点的内部变量只有在能精确推导同一个已给定整体取代值时，才从计算连接点列表中分离；部分取代后的标签只有在保留变量能证明其与真实标签的关系时才归一。最多8个变量、256种部分替换，未知字段不丢弃、基团矛盾拒绝。

现有经骨架验证的位置适配器接受封闭字段别名；只有整体基团明确时补齐其确定的元素/氟化模式，绝不猜环上位置、Alk范围或新骨架。参数投影本身不是任意骨架适配，仍执行原连接位点、芳环、母核和化学合法性检查。原始/计算字段、等价标签及内部变量保存到assembly_provenance.assembly_input_projection，原文证据保持可追溯。

正式materialize、try_assembly及参考约束使用相同投影语义：有证据的等价格式允许通过，改变真实取代、位置或分子来源仍拒绝。apply_assembly依然仅应用成功且未过期的ID-only尝试，之后必须重新validate_result。通式局部候选与实例拼接共用local_group，避免两处符号展开口径不同。新模块属于post变化，上游复用不会因此重跑检测识别；Skill和Agent变化仍按原规则从结构审查续跑。

针对上一轮原始快照的确定性回放，25个编号成员全部计算完成，第一系列10个有10个不同结构；化合物3、26与本图对应独立识别的完整结构吻合。原图未编号开放Alk候选仍不猜测，这与已明确具体编号成员的成功拼接分开统计。正式工作台新运行及最终结果另记。


补充修复独立审查来源边界：structure_audit 0.1.4新增reference_source_summary输入，包含已登记补充批次、目标端点、成员、实际查看来源与宿主来源约束错误摘要。不提供额外图像、识别SMILES或提取者对话；它不是化学真值证书。审查者在当前图范围内只核对这些外源成员沿用本图母核、编号和来源分离，不能仅因为未附参考图像而否定已登记的外源赋值；具体约束错误及后续化学质量检查仍保留。

此前真实运行wd_01M81RJHVV30GKZCBMC02T18YD使用0.1.3，误把已应用外源成员判为无依据，未进入固定回填；该测试请求阶段边界暂停，状态paused，不作为拼接失败率样本。它没有覆盖原始已确认取证快照。新验证从原wd_01M81NDAJZ3R9N7PVEBSD1Z9FT继续，不沿用旧审查试跑的临时修改。

拼接确定性验证见samples/outputs/figure2-assembly-projection-replay.json：包含25个编号实例的完整结构、原始/计算参数投影、源快照哈希与未解决开放候选；source_snapshot_unchanged、original_input_unchanged均true，来源约束0错误，第一系列10个独特结构，3/26与独立识别一致。118项拼接相关回归通过；局部通式/审查/取证37项通过；新增审查摘要后的相关47项通过（测试存在重叠，不累加）。一项旧的语义失败恢复测试补上现有必需的独立审查假响应，未取消或绕过真实审查门禁。


正式验证完成：wd_01M81S1S0FCSGKQW5W2RER6V8V使用structure_audit 0.1.4，独立审查passed，固定后处理与report完成，无运行异常。检测/识别/编号图全部沿用，沿用两批全文来源，不调用scheme_extract生成新结构。25/25编号成员拼接完成，25个均有assembly_input_projection，第一系列10个独特结构，3与26和独立识别的完整结构一致；原始ID-only提取与来源快照相同，参考约束0错误。与上一轮15/25相比，修复了全部10个assignment_variable_absent_from_template失败。

独立结果：samples/outputs/figure2-assembly-projection-reviewed-agent-final-result.json；检查记录：samples/outputs/figure2-assembly-projection-reviewed-agent-rerun-validation.json。整体needs_review仍保留：8个未编号开放Alk候选无法穷尽、既有information_gaps/总状态及库存等质量问题尚待修正，不能把25个编号实例计算完成宣称为整图所有质量检查已通过。

本次真实模型曾请求被禁止的全量提取和提前结束，均被宿主拒绝，exploration最终no_progress；固定后处理在精确版本的独立审查已pass后正常执行，实际scheme_extract调用为0，结果不是重新生成JSON所得。据此进一步收紧后续运行可见工具：已有合成路线后不再向模型提供extract_figure，当前版本未校验前不提供finish；底层事务与结束门控继续保留。原生调度回归断言续跑请求中不包含这两个不可用工具，不把旧运行的no_progress改写为正常结束。

新增审查来源摘要协议调度用例随21项独立审查测试通过；此前118项拼接相关回归、37项通式/取证/审查回归及47项来源摘要相关回归通过（有重叠，不累加）。最终可见工具修正的回归结果另记。

最终工具可见性及拼接/审查/原生探索回归61项通过（含续跑时extract_figure与未校验前finish不出现在模型工具列表的断言）。格式和Ruff检查通过；最终服务已重新加载，后端健康与局域网页面均返回200。


### 每个成员的显式attachments清单（2026-11-22，策略6.6）

按用户明确的设计，attachments是具体成员/组合内的键数组，不是新的基团对象。substituents仍可以同时包含X、Y、Z及XCHYZ，attachments=["XCHYZ"]仅选择整体基团；多独立点列多个实际标签。结构化阶段确定清单，回填原样保留清单和值，拼接仅从被选键取值。未选字段保留作解释，其整数不绑定为基团分子ID。选中的整数仍必须来自当前图。位置及键类型、母核拓扑和化学合法性继续独立检查，不因有清单而绕过。

molecular_contract=16，scheme_extract 0.13.0、mol_backfill 0.7.0、structure_audit 0.2.0。CompoundInstance加入attachments，8个共享输出Schema同步兼容；新Skill指令与受维护JSON示例明确填写清单。record_reference_assignments、try_assembly支持数组，来源应用/局部补丁保留选择，已有来源明确指定的选择不能静默改为另一组字段。独立审查及宿主attachment_selection_gaps检查每个待拼接实例；即便VLM给pass，缺失清单仍needs_revision，阻止进入正式回填。

显式清单非空且唯一，键必须存在于substituents并匹配当前母核实际连接点，缺少实际位点也明确失败；不自动将SCHYZ等取代后名字猜成XCHYZ，不从未选字段推断另一个选择。后处理provenance保存attachments及selection_mode=explicit_attachments。原图通式候选由已声明的expression确定实际位点，计算生成的assembly_candidates也带对应清单；其来源与编号成员仍分开。

兼容策略：旧JSON未提供清单时可继续按legacy_projection读取，明确记录模式；schema中的可选/null仅用于历史数据。新的FigureAgent结构审查要求每个待拼接实例补齐非空数组，不在回填时悄悄生成它。旧数据需要从结构阶段局部补数组及必要的规范连接点字段，再回填；原文解释字段不删除，不重新提取整张图。纯mol_id完整分子不要求清单。

样例验证：samples/outputs/figure2-explicit-attachments-validation.json。在原快照副本上先进行独立的结构兼容准备（补数组、补实际位点键并规范已证实的位置字段），保存逐成员before/after；随后25/25编号成员使用explicit_attachments拼接完成。选择与substituents被回填完整保留，来源约束0错误，第一系列10个独特结构，3/26与独立识别一致；原历史快照和准备后的输入均未被后处理修改。本次是确定性兼容/拼接验证，没有重新调用LLM或改写历史运行。开放Alk候选仍待复核，未冒充已确认编号成员。

首组108项拼接/来源/结构回归通过，后续82项清单/探索/复用回归通过（有重叠，不累加）；最后新增的“审查误通过仍被宿主拦截”与共享候选回归另记。设计文档与现有Skill直接更新，没有新增usage文档。

最后新增宿主拦截测试及共享回填/通式回归74项通过（与前组有重叠）；新版Skill包加载、现有Scheme响应示例Schema验证、Ruff检查和格式检查通过。服务已重新加载attachments契约，历史数据未重写。
