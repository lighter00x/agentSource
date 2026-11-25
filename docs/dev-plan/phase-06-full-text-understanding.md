# P6：全文理解与产出

> 周期：D8～D11 ｜ 负责人：BE-4（DocLookup、ReaderAgent）、BE-2（化学归一、step_builder、投影）、BE-3（正文抽取、导出）
> 前置：P5 的 FigureReport 与测定；P4 的化合物台账
> 设计文档对应：7.8 ReaderAgent、7.9 DocLookup、8.9～8.12 正文抽取/OPSIN/归一/路径与步骤、11.2～11.3 路径与投影校验、14 投影与导出

---

## 1. 目标

在图像理解结果的基础上完成全文理解，产出用户指定格式的成药性 JSON：

```text
正文事实抽取 ─┐
OPSIN 名称解析 ─┼→ 化合物归一（完整）→ ReaderAgent 逐页阅读（按需批量回查）→ 综合出 PathProposal
FigureReport ─┘                                                        ↓
                                        step_builder（RDKit 片段差异、变化分类、校验）→ Profile 投影 → 导出
```

## 2. 范围

**做**

- `text_extract`（T1）：按章节块抽取编号提及、测定值、实验条件、叙述语句，全部带 `char_span`。
- `name_to_structure`：实验部分"名称 (编号)"切分、OPSIN 解析、HRMS 分子式比对。
- `compound_resolve` 完整版：加入 OPSIN、分子式校验、立体变体、冲突生成、角色初判。
- DocLookup：TextLookup 批量回查（规划 1 次 + 作答 1 次 + 零调用快路径）、VisionLookup（按页合并）、缓存。
- ReaderAgent：页包构建、页面分类、逐页阅读、笔记增量合并与校验、待查队列与批量触发、代码压缩、每页 checkpoint、综合阶段。
- `step_builder`：MCS 片段差异、变化类型判定、环名词典、多处修改拆分（尽力）、串联校验；V-P01～V-P08。
- Profile `drug_optimization_v1` 投影与 V-J01～V-J05；导出（交付 JSON、审计 JSON、Corpus Markdown、审计 Markdown、measurements.csv、chunks.jsonl、manifest）。
- 评测脚手架 v0：与银标准（现有流水线产物）对比。

**不做**

UI（P8）、从第 k 页重读的 UI 入口（P8，本阶段提供 API）、笔记的 LLM 压缩（尽力）。

## 3. 目录与模块

```text
drugpaper/
├── skills/builtin/text_extract/  name_to_structure/
├── chem/
│   ├── opsin.py                  # 内嵌 JVM 或 HTTP
│   ├── diff.py                   # rdFMCS 片段差异
│   ├── change_classifier.py
│   └── ring_lexicon.py
├── facts/resolver.py             # 完整版
├── agents/lookup/
│   ├── batch.py                  # 批次：预处理、规划、并行执行、作答
│   ├── fast_path.py              # 零调用快路径
│   └── vision.py                 # 按页合并的视觉回查
├── agents/reader/
│   ├── page_pack.py  notes.py  queue.py  graph.py  synthesize.py
├── profiles/
│   ├── projector.py
│   └── drug_optimization_v1/profile.yaml  output.schema.json
├── validators/path.py  projection.py
├── exporters/
│   ├── json_export.py  corpus_md.py  audit_md.py  measurements_csv.py  chunks.py
└── evals/silver.py
config/ring_lexicon.yaml
```

## 4. 关键设计要点

### 4.1 DocLookup 批量回查

| 步骤 | 说明 | 模型调用 |
|---|---|---|
| 预处理 | 抽取编号、图表标签、页码、关键词；合并重复问题；命中缓存的直接返回 | 0 |
| 快路径 | 可直接映射到工具的问题（"12a 出现在哪"、"26 的 hERG 值"）由代码直接作答 | 0 |
| 规划 | 为剩余问题生成检索动作；只附预选工具的 L1 短规格（P3 的 CapabilityRouter） | 1 |
| 执行 | 代码并行执行全部检索动作 | 0 |
| 作答 | 逐题回答，给出证据位置；找不到的标 `not_found` 或 `need_vision` | 1 |
| 视觉 | `need_vision` 的问题按页合并，每页 1 次 VLM | 每页 1 |

约束：只读、不嵌套、同用户同文献、不等待其他 Agent、同文献同问题缓存复用。

### 4.2 ReaderAgent

- **页包**：本页段落（跨页段落只放在起始页）、本页图表的 FigureReport 摘要、本页 T1 事实、脚注；上限约 7K tokens。
- **页面分类**：参考文献、致谢跳过；实验部分只做规则略读（名称、编号、HRMS 写入台账）；正文调用模型。
- **读页调用**不带工具，只输出 `NoteDelta`；由代码合并，校验页码指针和编号存在性。
- **待查队列**：新问题入队去重；被后续页面解答的问题自动移除；满 5 条、出现阻塞问题、进入新章节或进入综合时批量回查；答案随下一页的页包一起给出。
- **笔记**：上限约 8K tokens；超限时由代码压缩，只丢细节不丢页码指针。
- **checkpoint**：每页一个笔记版本，支持续读和"从第 k 页重读"。
- **综合**：笔记 + 化合物档案 → `PathProposal`（输出结构与规则同设计文档 8.12）；综合前做 1 次批量回查。

### 4.3 step_builder 与路径校验

- rdFMCS（`ringMatchesRingOnly`、`completeRingsOnly`）求公共子结构，差异原子聚成变化区域，扩展 1 个键的上下文生成片段。
- 变化类型判定：环替换、N-去烷基化、卤代、甲基化、羟基化、环化/开环、链长变化、立体变化。
- V-P01～V-P08 全部实现；失败信息回灌 ReaderAgent 综合阶段，最多 2 次。
- 多处修改拆分生成虚拟中间体（尽力完成，可削减）。
- 片段的权威校验是子结构匹配 + 原子索引，尽量同时满足字符串子串。

### 4.4 投影与导出

- 按 `profile.yaml` 把事实层确定性地投影为用户指定的 JSON 结构；`分子数据指标` 以最终产物为主体。
- 所有 E-SMILES 原样输出（V-J03）；properties 的 value 不含编号，`来源` 在最后（V-J04）。
- 交付版严格保持用户结构；审计版附每个字段的证据和来源。
- Corpus Markdown 的图像描述位置与现有流水线一致，便于对照实验。

## 5. 任务分解

| # | 任务 | 负责 | 估时 |
|---|---|---|---|
| 6.1 | `text_extract`：章节切块、结构化抽取、`char_span` 校验（V-E02）、区间展开 | BE-3 | 0.75 天 |
| 6.2 | OPSIN 集成、`name_to_structure`、HRMS 分子式比对 | BE-2 | 0.5 天 |
| 6.3 | `compound_resolve` 完整版 | BE-2 | 0.5 天 |
| 6.4 | DocLookup：批量、快路径、规划/作答、缓存 | BE-4 | 0.75 天 |
| 6.5 | VisionLookup（按页合并） | BE-4 | 0.25 天 |
| 6.6 | 页包构建；页面分类；按页序等待就绪 | BE-4 | 0.5 天 |
| 6.7 | ReaderAgent 阅读循环、NoteDelta 合并与校验、待查队列、代码压缩、checkpoint | BE-4 | 1 天 |
| 6.8 | 综合阶段与 PathProposal；校验失败回灌 | BE-4 | 0.5 天 |
| 6.9 | `step_builder`：MCS 差异、变化分类、环名词典、串联校验、V-P01～V-P08 | BE-2 | 1 天 |
| 6.10 | 多处修改拆分（尽力） | BE-2 | 余量 |
| 6.11 | Profile 投影、V-J 校验 | BE-2 | 0.5 天 |
| 6.12 | 导出：JSON、审计 JSON、Corpus/审计 Markdown、CSV、chunks、manifest | BE-3 | 0.75 天 |
| 6.13 | 银标准对比脚本 v0 | BE-3 | 0.25 天 |

## 6. 测试

| 类别 | 用例 | 期望 |
|---|---|---|
| 正文抽取 | 摘要中的 "compound 26 [UCM-1306 ...]" | 生成别名关系；`char_span` 可回到原文 |
| OPSIN | 实验部分化合物 26 的系统名 | 解析出的结构与 OCSR 一致；分子式 C14H14FNO2S 与 HRMS 一致 |
| 回查 | 一批 6 个问题，其中 3 个可走快路径 | 模型调用 2 次，快路径的 3 个不调用模型 |
| 回查 | 同一文献中重复的问题 | 命中缓存 |
| 阅读 | FakeLLM 逐页输出 NoteDelta | 笔记合并正确；证据节点不存在的记录被拒绝 |
| 队列 | 第 3 页提出的问题在第 4 页被解答 | 不触发回查 |
| 断点 | 读到第 6 页时中断 | 从第 6 页的笔记版本续读 |
| 路径 | 构造"苯基 → 嘧啶基"但文字写"吡啶" | V-P06 失败，反馈进入综合阶段 |
| 路径 | 声明"去甲基化"但环组成变化 | V-P05 失败 |
| 串联 | steps 首尾与 lead/final 一致 | V-P01 通过；投影后 V-J02 通过 |
| 投影 | 含 `<sep><a>0:R1</a>` 的 SMILES | 导出逐字一致 |
| 端到端 | 样例论文 | 最终产物为化合物 26（UCM-1306）；每个非 null 字段都有证据 |

## 7. 验收标准

- [ ] 样例论文产出合法的交付 JSON 和审计 JSON，V-J 全部通过。
- [ ] lead 与 final 识别正确；steps 可串联，片段校验通过（或明确标记 needs_review）。
- [ ] 每个非 null 事实字段可回跳到节点或 bbox。
- [ ] 10 篇银标准论文的对比报告：最终产物 InChIKey 匹配率、lead 识别率、指标召回率（作为基线）。
- [ ] 单篇调用数中位数与设计文档 7.6 的预算对比，偏差 > 30% 时分析原因。
- [x] 接口冻结：`PagePack`、`NoteDelta`、`LookupBatchResult`、`PathProposal`、`Step`、Profile 格式、导出文件格式（0.6.1，仅开发契约；不代表其余实文验收通过）。

## 8. 风险

| 风险 | 对策 |
|---|---|
| ReaderAgent 在 15 天内不稳定 | 兜底：综合阶段退回"化合物档案一次性推理"，接口不变 |
| 逐页阅读串行耗时过长 | 与 FigureAgent 按页重叠；页面分类减少调用；必要时两页一读 |
| MCS 在大分子上计算慢 | 设置超时；超时的步骤保留 LLM 描述并标记 needs_review |
| OPSIN 部署问题（JVM） | 可选 HTTP 服务模式；OPSIN 不可用时跳过该校验，不阻断流程 |

## 9. 交给下一阶段的内容

- P8：阅读笔记、回查记录、PathProposal、投影结果，供 UI 展示；"从第 k 页重读"的 API，供重跑规划器调用。
- P9：评测脚手架与基线，供对照实验与回归。

## 完成记录

（阶段结束时填写）

### 2026-10-05 起步记录

- 已启动 S6.0：公开 `text_extract` Skill、正文四类候选、编号区间/UCM 别名规则、V-E02、raw/norm 偏移与唯一原文引文定位、长段切块、实验名称证据、原子入库与稳定缓存。模型偏移修复与丢弃均明确标待复核。
- 已启动 S6.4：起始页段落、脚注、图表摘要与正文产物，页面分类、7000-token 阅读单元、缺失依赖与待复核状态保护。尚未接 ReaderAgent 阅读循环。
- 命令 `drugpaper text-facts --doc` 和独立样例验收 `make verify-p6` 已提供；样例回放与一次真实摘要证据见 `samples/outputs/phase-6/20261005-foundation-final/`。
- 本次不勾选第7节整阶段验收；OPSIN、DocLookup、完整归一、ReaderAgent/恢复、路径/投影/导出和十篇银标准仍待实现。P5十篇真实整体验收仍未通过，页包明确阻断失败/缺失结果，并保留复核标记。


### 2026-10-05 接续开发记录

- S6.1/S6.3 代码链路接入：可选OPSIN HTTP、名称与HRMS组成校验、候选/标签原子投影、正文台账、优先级与冲突、初判角色/证据、版本/人工锁/过期结构保护。现有text-facts命令可执行新链路。
- 样例四条标题真实解析3条；26的OCR名称失败，现有fixture无HRMS，样例26接受OPSIN并与OCSR一致的内容验收未通过。正文模型仍回放，不能作为召回率。真实乙醇及受控26分别提供成功路径证据。
- 证据与测试：samples/outputs/phase-6/20261005-chemistry/completion.md。S6.2、S6.5～S6.12、正文测定映射、OPSIN正式部署及全文交付验收仍待接续；保持in_progress。

### 2026-10-05 全文开发交付记录

- 已接续全部必需开发模块：独立OPSIN服务交付件、批量DocLookup/按页VisionLookup、正式正文测定/条件投影、Reader笔记/队列/压缩/每页版本/断点恢复与重读、综合PathProposal、MCS及九类变化与V-P01～08、两次反馈重试、固定旧用户JSON/Profile与V-J01～05/V-E01、完整导出/API/CLI/Celery及0008迁移。
- 尽力拆分生成可sanitize的虚拟中间体和split_steps审计候选，不把虚拟物当文献实物，原多区域步骤保留needs_review。笔记使用保留全部证据指针的代码压缩，尽力项LLM压缩未启用。
- 收录10份真实历史Gemini完整银标准JSON及来源哈希；评测报告显式记录独立预测覆盖0/10，缺失预测非零退出。尚不能计算真实lead/final匹配率、真实指标召回或单篇调用中位数。
- 受控完整链路V-P/V-J/V-E通过，lead=1/final=26、3模型调用/0工具、12个ZIP文件哈希通过；这只是实现验证。877项单元/集成测试全通过，Ruff/557文件格式/mypy278源文件通过。接口0.6.1的11模型、Profile、OpenAPI及SHA256清单已冻结。
- 第7节仅勾选接口冻结。P5十篇整体未通过、样例26系统名失败/HRMS缺失、真实Reader人工核验和10篇独立预测仍待验收，阶段保持in_progress，未打p6-done。逐项映射见 `execution/phase-06-execution.md` 最后记录；证据见 `samples/outputs/phase-6/20261005-full-text/completion.md`。
