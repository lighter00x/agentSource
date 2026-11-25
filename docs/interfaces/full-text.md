# P6 全文理解与交付接口（0.6.1，2026-10-05）

S6.0～S6.12 的开发交付件已接入，Schema、Profile 和 OpenAPI 已冻结为 0.6.1。开发验证与真实文献内容验收分开记录；P5 十篇整体验收、样例26名称/HRMS和十篇独立预测仍未通过，阶段状态保持 in_progress。

## 调用入口

- 命令：`uv run drugpaper text-facts --doc <document_id> [--force]`，依次处理正文、实验名称、化合物归一、测定映射与页包。结果包含 `name_runs`、`compound_resolution`、`text_measurements` 和 `p6_complete=false`。
- 全文命令：`uv run drugpaper understand --doc <document_id> [--no-prepare-text] [--from-page k] [--export-dir <dir>]`。正文准备后顺序阅读、综合、步骤校验与回灌、确定性投影；缺失 P5/T1 材料返回 pending，不生成猜测的全文结果。页码从0开始。
- 公开 Skill：`text_extract`，`target_ref` 为当前 revision 的 paragraph/list，`inputs={}`。通过现有 find/load 能力工具发现、加载，SkillTool 使用同一 Runtime 和原子提交。
- 页包：`load_page_pack(db, document_id, page, user_id="default")`，页码从 0 开始；校验文档归属及当前 revision。
- 开发验证：`make verify-p6` 在独立 SQLite 中回放真实样例正文，并运行明确标为 controlled_fake_model 的完整链路；`--live` 额外调用一次真实摘要任务。报告明确 `p6_complete=false`，不把回放当作真实召回率。

## 正文产物

类型 `text_extraction`，格式见 `phase-6/TextExtraction.schema.json`。

模型读取由 `text_raw` 重新确定性规范化的 `text_norm`。条目 `char_span` 是规范化文本的左闭右开字符区间；`evidence[].char_span` 是映射后的原文区间。模型自带的节点引用不被采纳。编号、别名、指标、数值、单位、条件和叙述原句必须在证据区间中出现。

模型难以准确计数字符时，只有在当前原文中唯一出现的逐字 `evidence_text` 才可重新定位。重复引文、错误数值、错误编号不会通过。修复计入 `span_repair_count` 并保持 `needs_review`；无可核验依据的条目丢弃并计入 `discarded_count`。统计针对模型候选，另由规则补齐显式编号区间和 UCM 别名。

按段落处理，长段落优先按句分割，每块不超过 2800 估算 tokens；偏移合并回原段。单个节点最多 12 次逻辑模型调用，超过预算显式失败，不能留下部分产物。引言只做规则提及，未知章节与参考文献跳过。实验名称保留原文证据交给后续 OPSIN，不宣称名称已转换为结构。

缓存指纹包含当前正文、节点类型、章节角色、包内容及模型配置，不依赖自身旧产物或后来更新的化学台账。提交时再次验证正文与 revision；正文改变则整笔回滚。产物的 source_text_sha256 由服务端计算，页包再次核对当前原文；编辑后的旧产物不能继续作为就绪材料。正文 Skill 不要求准备 PDF 裁剪图。

## 阅读页包

格式见 `phase-6/PagePack.schema.json`。

- 跨页段落只放在起始页；脚注保留。正文节点保留原文偏移；大页拆为多个有界单元，每个至多 7000 估算 tokens。
- 参考文献/致谢页 `skip`，纯实验页 `skim`，其余 `read`。
- 本页所需正文产物或 FigureReport 缺失、失败、归属不符时 `ready=false`，列出 `pending_refs`。
- `needs_review` 的正文/图表结果可以作为阅读材料，同时明确保留状态、警告、图像未核验字段和待查问题。就绪只意味着材料已提交，不能作为内容正确性认证。
- 图表摘要带产物 ID 和前十行要点；超出单元预算的单个事实或摘要显式报错，不截断证据或吞掉复核状态。


## 实验名称与 OPSIN

公开 Skill `name_to_structure` 使用当前 revision 的实验 paragraph/list，inputs={}，不准备图像，不调用 LLM。段首必须具有“系统名称 (编号).”；非实验章节或缺少标题返回 skipped。名称与编号证据从原文规范化映射，HRMS 只关联同一标题段。

配置 `OPSIN_BASE_URL`（包含服务路径）、`OPSIN_TIMEOUT_S`、`OPSIN_MODEL_VERSION`。服务兼容 [官方 OPSIN JSON 协议](https://www.ebi.ac.uk/opsin/)，GET `<base>/<URL编码名称>.json`，解释 SUCCESS/WARNING/FAILURE。默认 URL 为空，不自动使用公共服务，不启动 JVM。平台实际部署可使用兼容的内网 HTTP 服务；更新服务版本时修改 `OPSIN_MODEL_VERSION` 或 force。

产物为 `name_to_structure`，Schema 见 `phase-6/NameExtraction.schema.json`。保留 OPSIN 原始 SMILES，RDKit 派生 canonical SMILES、InChIKey 与 formula；候选与 label_claim 同 Artifact 原子提交。名称解析成功不是独立化学正确性认证。服务异常、解析失败、WARNING、无效结构、公式不一致或缺少 HRMS 均保持 needs_review。成功且公式一致可缓存；待复核结果允许重新请求，防止故障永久命中缓存。空结果重跑也撤销旧 active 候选/标签，历史版本保留。

`check_hrms` 用元素计数比较，不依赖元素书写顺序。支持中性式及 `[M+H]+`、`[M-H]-`、`[M+Na]+`、`[M+K]+`、`[M+NH4]+`；同时记录 `formula_basis=neutral/adduct/mismatch`。没有公式、复杂加合物、盐和同位素公式返回 `formula_match=null`。这是组成校验，不是精确质量验证。原文、章节或 revision 在运行中改变则整份回滚。

## 化合物台账

公开 `compound_resolve` 升级为 0.6.0，保持现有输入/输出结构与 P4 数据表。合并当前 T1 提及、别名、名称候选与图像标签；剔除失效、移除及旧 revision 材料。显式结构 ID 不存在时不会替换为同节点其他结构。人工锁、局部 scope、稳定编号 ID 与立体父子关系沿用。

优先级：人工100、OPSIN公式一致90、OCSR公式一致/高置信80、未验证OPSIN70、Markush60、低置信OCSR40、公式不一致OPSIN30、LLM10。WARNING/无效/Markush占位结构不参与采纳。低置信候选可入台账，复核状态随产物保留；公式冲突、连接/立体冲突、别名歧义显式生成证据。歧义别名不采用最后一次出现的编号，未锁定的旧别名会同步清理。

`Compound` 增加 `evidence`、`role_evidence`、`role_status`，快照见 `phase-6/Compound.schema.json`。T1 的 hit/lead/final 角色提示以及摘要/结论重复编号只作初判，role_status=needs_review；Reader 综合结果中的 PathProposal 单独记录最终角色及路线，不覆盖人工台账。重算清理过期自动证据与角色提示，保留人工锁。提交前复核正文、名称、结构与人工台账快照。只读结构查询不返回过期 OPSIN 候选；引用回填不采纳 WARNING 或未验证 HRMS 的 OPSIN 结构。

## 本轮验证与边界

`make verify-p6-chem` 默认回放设计契约中的受控标题，不访问业务数据库。显式真实服务：`uv run python -m scripts.verify_phase_6_chemistry --opsin-url <url> --control ethanol --out <独立目录>`。样例四条实验名称可通过 `verify_phase_6 --opsin-url <url>` 运行；正文模型仍回放，除非另外指定 --live。

证据：`samples/outputs/phase-6/20261005-chemistry/`。样例有四条标题，官方服务返回三条候选；26 的 OCR 名称含 `·`，解析失败。现有样例未发现 HRMS 文本，三条候选保持待复核。设计文档中的26名称另因 S 位点失败，原始失败报告保留；未自动改名或伪造成功。乙醇真实成功链路及受控26回放通过，不能替代样例26/SI 内容验收或十篇评测。

## 正文事实映射

`facts/text_measurements.py` 将当前 T1 的测定与实验条件投影为 Measurement/Assay；保留编号、原始值、单位、条件、物种与证据，派生字典指标和 SI 值。编号/别名必须唯一归属；未映射指标、近似匹配、冲突、修复偏移保留 needs_review。以 Artifact 为单位事务写入、重复运行幂等，旧正文或换版材料不能继续使用。优化叙述仍以带证据的事实进入综合输入。

## DocLookup

`DocLookup.lookup(questions, ctx)` 返回 `LookupBatchResult`。每批1～8题、最多20个工具动作、60秒；一般题共用一次 lookup_plan 和一次 lookup_answer，快路径零模型调用。规划只披露只读工具，禁止 Skill、嵌套 Agent 和写操作。允许 grep/search/get_node/get_context_window/get_page_text、化合物/测定/已提交图表查询；get_figure_report 不等待。

问题按 NFKC、大小写、空白和标点归一化；合并重复问题并按输入顺序返回。缓存键含用户、文档、revision、当前事实指纹、问题及视觉页码。正文、台账、图表、应用/Skill源码、提示词、Profile或模型配置变化使旧缓存、阅读检查点和全文结果失效。回答不超过150字，状态为 found/not_found/ambiguous/need_vision，证据必须来自本次工具返回且属于当前节点。失败不缓存。

视觉题按页合并为一次 vision_lookup，最长边1600px，不携带工具；证据限于对应页面。缺失页面图时保留待查，不伪造答案。lookups 与 RunRow 记录批次、实际模型任务/usage、工具次数和缓存命中。

## Reader、恢复与综合

ReaderAgent 按页序消费 PagePack。read 单元调用一次 reader_page，输入不含工具；skim 记录实验事实指针，skip 跳过参考文献/致谢。缺失材料默认 pending，可选择最多60秒的等待。NoteDelta 的页码、节点及编号逐项校验，来源限于当前阅读单元或合法回查答案；未知编号标未确认。

待查问题去重，后续页面自然解决的问题移除；达到5题、blocking、章节边界或结束时批量回查。代码压缩控制笔记至8000估算tokens，折叠描述但保留完整节点/页码指针和数据库原始delta。证据指针自身超预算时明确失败。尽力项 LLM 压缩未启用。

`reader_notes` 持久保存每页版本和active状态；并发写入验证前驱 checkpoint，PostgreSQL 使用文档事务锁。从第k页重读恢复第k-1页笔记，撤销后续active检查点，历史保留。源指纹变化阻断旧笔记恢复。进程中断后重建 Services 可恢复已提交页。

综合前最多一批确认回查。reader_synthesize 读取笔记、带证据的化合物档案和路线，限制12000估算tokens，输出 PathProposal 的实体ID、lead/final、主路线、策略、SAR、标签和总结；模型不提供结构字符串。原文主路线与旁支SAR区分，结论必须有直接证据。

## 路径与投影

RDKit MCS 限时10秒且匹配完整环；差异按连通区域分组，记录一键上下文片段和原子索引。分类支持环替换、N-去烷基化、卤代、甲基化、羟基化、环化/开环、链长和立体变化；环名词典记录中英文名称、大小及杂原子组成。

V-P01～V-P08 校验串联、实体/片段、单区域、原文方向与直接关系、声明变化、环名、叙述连续性及结构冲突。共处一表不能作为实际优化路线证明。多区域变化尽力生成 sanitize 通过的虚拟中间体和逐步 split_steps；它们进入审计候选，原始多区域步骤保持 needs_review，不能冒充文献实物。

FullTextPipeline 使用 LangGraph 的 reader_read → path_reason → step_builder → project；校验失败最多再综合两次，总计三次，反馈与每次路径保存在 RunRow。用尽次数后仍可投影待复核结果，所有错误保留。

`drug_optimization_v1` 使用旧用户 JSON 的固定字段，`先导化合物分子信息`、数字键 steps、对象 end 和固定指标，缺失值为字符串 `"null"`。确定性投影保留原始 E-SMILES，未解结构用 compound label，Markush 保留 scaffold。指标以最终化合物为主体，按物种/条件选择；冲突或不可比结果不合并，所有其他测定保留在审计与长表中。同条件比较不足3项时告警，不编造。

V-J01～V-J05 校验 Schema、路径、原始结构、properties/来源位置及中文叙述；V-E01 验证每个非null字段与审计值一致且具有当前一手证据。审计版保持交付版形状，每个叶子为 `{value,evidence,source_ids}`，`_audit` 另存组件版本、源指纹、路径候选与校验告警。任何上游待复核材料使全文结果保持 needs_review。

## 持久化、API 与导出

迁移0008新增 lookups、reader_notes、full_text_results、exports；所有记录有 user_id，SQLite/PostgreSQL 升降迁移均受测试覆盖。主文档管线可通过 understand_full_text 启用，Celery 提供同名任务。该交付未自动迁移业务数据库。

API 前缀 `/api/v1`：

| 方法与路径 | 用途 |
|---|---|
| POST /documents/{id}/lookups | `{questions:[{question,kind,target_page,blocking}]}`，每批最多8题 |
| POST /documents/{id}/reader | 顺序阅读，body可含 from_page |
| POST /documents/{id}/reader/reread | from_page 必填，恢复前一页版本 |
| POST /documents/{id}/full-text | `{prepare_text,force,from_page}`，完整链路 |
| GET /documents/{id}/full-text | 当前revision和指纹的最新结果 |
| POST /documents/{id}/exports | `{result_id}` 可省略，导出当前结果 |
| GET /exports/{id}/download | 私有 BlobStore 中的 ZIP |

服务沿用当前 default 用户入口；查询和资产有归属/删除/版本检查，用户鉴权属于P7。

导出包括 result.drug_optimization_v1.json、同形审计JSON、paper.corpus.md、paper.audit.md、document.json、compounds.json、measurements.csv、chunks.jsonl、images/、已有曲线的 charts/*.csv/overlay.png 及 manifest.json。Corpus 的结构化图像描述紧随图片；chunks 以章节段落/化合物/字段事实组织，文本最多800tokens。缺失图像/曲线不制造占位证据。

manifest 记录源码/Skill/提示词/配置/Profile哈希、模型调用、Artifact版本、revision、校验与告警及所有其他文件的SHA256。ZIP 固定排序/时间戳，可复核。同一源指纹才可导出；Schema/原始结构/证据错误阻断导出，其余待复核项随包保留。manifest 不包含自身哈希。

## 银标准、冻结和复现

`tests/fixtures/silver/` 收录10份真实历史 Gemini 完整JSON，manifest保留路径和SHA256。这些是未人工复核的银标准，不是金标准，也不是系统预测。

`uv run python -m scripts.evaluate_silver --predictions <独立预测目录> --output <报告目录>` 比较 lead/final InChIKey、标签重合、指标召回/值一致、步骤数量/串联。缺失预测进入分母和明细，报告不完整退出2。当前已生成10篇缺失明细，预测覆盖0/10；尚不能报告识别率或真实调用中位数。

`uv run python -m scripts.verify_phase_6_full_text --controlled --out <隔离目录>` 验证 FakeLLM 完整链路。真实文档必须用 `--document <id>`，要求已提交P5报告，并由人工核验内容；受控 final=26 不能替代样例结构或真实lead识别。`uv run python -m scripts.freeze_phase_6` 重建 phase-6/ 的11个模型、Profile、OpenAPI和SHA256清单。

真实样例十题定位复现：先运行 verify_phase_6 生成独立 foundation.db，再运行 `uv run python -m scripts.verify_phase_6_lookup --database <foundation.db> --output <report.json>`。固定题目覆盖Figure1～8图注、Table3图注及UCM-1306出现页；答案采用既有get_node规范化文本契约，最多150字。记录10/10、零模型调用和重复八题缓存命中，不认证图中内容或通用推理。

最终证据见 `samples/outputs/phase-6/20261005-full-text/completion.md`。真实 OPSIN JVM 服务与名称Skill/HRMS组成/采纳链路已在隔离环境验证，部署交付件见 services/opsin/；正式业务部署、Docker镜像构建与真实样例内容验收未执行。不打 p6-done。
