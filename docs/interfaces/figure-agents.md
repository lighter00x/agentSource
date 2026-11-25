# P5 图像 Agent 接口（0.5.0）

2026-11-20 实现补充：以下需求说明保留确认时的基线；基础调试能力已通过独立 `/documents/{id}/workbench` 接口接入，详见 [监控文档的工作台接口](monitoring.md#基础工作台接口2026-11-20)与 [P8 最新交付记录](../dev-plan/execution/phase-08-execution.md#2026-11-20-基础版本实现与验证记录)。原冻结接口和完整阶段验收结论不变。

## 2026-11-20 配套调试需求（待实现，不修改本页冻结接口）

需求详见[总体设计](../drug-paper-agent-detailed-design.md) 13.7/17.6 与[P8 执行方案](../dev-plan/execution/phase-08-execution.md)。新增语义需要补充 API/Schema 和验证后再冻结：

- 手动模式 MinerU 后停止，单图「解析」自动分类、路由、执行；另有独立分类/指定 Skill 的调试入口。类型人工覆盖保留机器原值，按新类型重新路由。
- 自动模式处理所有图表，单图 failed/needs_review 不阻塞其余图；文档汇总逐图结果，可仅重试问题图。
- 每步可查完整过程与实际 Skill/工具代码版本，支持执行到、执行前暂停、继续和指定来源运行的阶段重跑。复用正确的检测/识别后只做提取，不得仅依靠 force=true 重跑整个图代替。
- 分子框与 SMILES 支持人工修正及恢复；默认使用人工版本，追踪受影响下游。检测默认按需，可独立启动。图表跨页作为整体；本期不增加父子图调试界面，也不删除既有 Panel 产物。
- 每次运行固定局部 Skill/工具/模型和上游快照，保留历史；暂不加入单元格/提取字段或分子关联编辑。下文的既有缓存与崩溃恢复不表示上述交互接口已具备。

## 数据与入口

JSON Schema 快照位于 `phase-5/`：FigureReport、TableGrid、ChartDataset、Measurement、Assay、ReviewResult，检查码、路由及 SHA256 清单一起冻结。ReviewResult 是兼容历史数据的消费模型；ReviewResult.producer.schema.json 是更严格的模型输出约束，要求每项给出 reason 和 suggested_value，错误数值必须给出非空建议值。HTTP 快照为 `openapi-p5.json`。新增迁移 `0007_figure_agents`，包含 assays、measurements、figure_relations、figure_reports、batches。

- `FigureAgent.run(node_ref, user_id="default", blackboard=None, mode="full", questions=None, force=False) -> FigureReport`。
- `FigurePipeline.run(document_id, user_id="default", force=False) -> dict`：黑板 v1 → LangGraph Send FigureAgent → compound_resolve → 黑板 v2 → 一轮定向 query → 汇总，质量统计写 documents.quality_report.figure_agents。
- `DocumentPipeline(services, understand_figures=True)`：在解析、IR、渲染与索引后接 FigurePipeline；已解析文档跳过解析并继续理解。默认保持仅解析，避免上传时隐含触发模型费用。
- `drugpaper figures --doc <document_id>`；`POST /api/v1/documents?understand=true` 将上传文档的完整流程送入队列。
- `POST /api/v1/batches`：`{"document_ids":["doc_..."],"queue":"batch"}`，queue 可为 interactive；202 返回 batch_id/task_ids。GET /batches/{batch_id} 返回状态、逐文档结果；派发失败保留 batch_id 和已派发任务，不盲目重投。
- 只读 GET /documents/{id}/figure-reports、/measurements、/assays。问答能力通过 `query_measurements`、`get_figure_report` 渐进发现，后者未提交时立即返回 pending。

## FigureReport 与调用纪律

status 为 ok / remediated / needs_review / failed；保留 extraction、四维检查、L2 verdict、未覆盖项 unchecked、open_questions、trace、artifact_ids、子 Panel 报告与 query_answers。needs_review 表示已完成处理但仍有未确认内容，不代表抽取正确。

full 路径有三次固定逻辑调用：visual_classify、链中唯一结构化 Skill、figure_self_check。检测、MolParser 识别、SOM、回填、映射、组装与 CV 为确定性步骤；低置信度复判、表格切片、未映射指标兜底单独计入 exception_calls。补救最多两轮、四次逻辑模型调用、180 秒；剩余调用和时长下传 SkillRuntime，禁止长表切片或重新分类绕过预算。超时/预算不足保留结果待复核。已完成结构化但首次自检失败同样保留结果并标记 needs_review；没有可用结构化结果才标记 failed。

accounting 包含 fixed_calls、exception_calls、remediation_calls、cached_steps、by_task、timings_ms 和 warnings。冷启动正常图为 3；缓存步骤实际调用数为 0；复用已提交报告时显示该报告所属原 run 的统计。调用口径是应用模型任务调用，供应商 SDK 的网络重试/结构化修复请求不等于新增任务，不能据此直接计算账单。

query 每图一次只读模型调用，答复定向问题而不改写测定；review 要求已有 full 报告，重新执行 L1/L2。Reconcile 每文档仅一轮、最多八个问题；同图问题合并。冲突答复保留证据和不确定性，不在 P5 强行消解。

## 渐进披露与安全边界

固定流程不把 Tool Schema 或全部 Skill 目录交给模型。分类后由代码加载当前类型的唯一结构化 Skill，模型只见裁剪图、当前框、限定结构和黑板摘录；全局 span 与其他文档原文不进入固定调用。大表/长曲线自检只发送本次核对字段及有限元数据，未送入的字段保持 unchecked。

补救按失败检查码确定性筛选 2～4 个相关 action_enum 能力卡；仅这些动作进入枚举。选中后加载该包并执行固定实现，ToolCaller 继续检查 manifest.tools 白名单，模型不执行代码。未披露动作、越界报告引用、非法修正路径拒绝执行并保留 needs_review。

apply_review_fix 仅应用封闭核对中的 incorrect/suggested_value，标 source=vlm_review；apply_text_layer 必须有唯一单元格几何命中，标 source=text_layer。人工锁和归属检查覆盖事实投影、缓存读取与 Panel 几何；事实与 Artifact 同事务，重跑停用旧事实，原始解析保留。

HTTP 用户认证延续项目阶段安排，P7 接入；内部 Tool/Skill/Agent 按可信 user_id 强制检查归属。当前 HTTP 默认 default 用户，部署接口仍绑定本机。

## 数值、图表与表格

Measurement 指向 table/figure 节点及 cells/series 字段，保留 value_raw、单位、误差、qualifier、状态与 Artifact 来源。Table 3 括号内 Kd 为独立次级测定，11 项主测定之外可产生第 12 条记录。未知指标保留 unmapped，图表数据为 estimated。Table1 的 potentiation of DA Emax 与 % maximum increase 使用两个不同指标；词典内容纳入相关Skill及图像/文档报告缓存键。

`to_si` 名称沿用计划，但浓度参考量是 mol/L（M），百分比是百分数点；其他量转 SI。消费者不能将 value_si 浓度误解为 mol/m³。图表坐标以模型看到的 1600px 图为基准；单元格 bbox 为裁剪图归一化坐标，切片合并时恢复到整图坐标。

长表按估计字高和图像缩放决定重叠行带切片，逐片计数并按文本/结构/跨度去重；SAR 表在同一次调用内附原图及标注图，避免编号遮挡文字；脚注 HTML 上下标会转为纯文本和 markers。900 秒长表总执行预算适用于初始结构化；补救仍受 180 秒总预算约束。

## 运行与恢复

部署 Worker unit：deploy/systemd/drugpaper-worker.service；容器模板为 services/agent/Dockerfile 与 Compose 的 agents profile。Celery batch/interactive、late ack、prefetch=1、reject_on_worker_lost，可见性超时 86400 秒。PostgreSQL 检查点在 lg schema，每步持久化；初始化通过非阻塞 pg_try_advisory_lock 轮询串行保护，避免等待锁的事务阻塞并发索引创建。SQLite 仅适合单进程测试，检查点只在内存，真实并发验收使用 PostgreSQL。

模型端点 Redis 租约信号量默认并发 8，心跳续租、取消释放、失联过期回收；测试无 Redis 时用进程内信号量。共享 YOLO Worker 保留每用户有界公平队列，应用内部可限时等待忙响应，公共检测接口的忙错误语义保持兼容。Worker 重投从相同 thread_id 恢复，已完成 Skill 缓存不重复请求。

真实 Worker 硬终止/重启验收采用独立测试队列和 FakeVLM，并在分类提交后中断：恢复后总调用仍为 3。若进程在外部请求完成而提交前被杀，外部服务不提供幂等键时无法保证该次请求不重发；不将此窗口宣传为 exactly-once。

## 2026-10-03 内容验收修正

表格模型调用内部使用 `CompactTable.transport.schema.json`：headers/rows 为字符串，footnote_markers 与稀疏 cell_details 必填，元数据再按 CellDetail 校验并展开为对外 TableGrid。标题由现有图注填入，模型不再输出 title，避免真实响应复制整表导致截断。输出预算4096；空脚注须显式给出空对象，不能靠默认值掩盖漏提取。

L2清单在数值已有PDF文本证据时仍要求核对表头、脚注、行列覆盖、方向和主体；producer 必须返回 items，拒绝额外字段并限制 reason 长度。新增 C_METRIC_UNMAPPED：数值识别正确但指标未映射仍不能通过。Assay保留脚注原文，解析LaTeX浓度；respectively对应4/24小时按关联行分别赋值。检查码快照共27项（25条L1、2条复核）。

FigureReport 的 ok/remediated 是内部处理状态，不代表独立内容验收通过。`scripts.audit_phase5_results` 对历史结果逐图审计；`scripts.verify_phase5_tables` 使用未提供给模型的人工参考值核对数据、脚注关联和Table3测定条件，任一项失败以非零退出。限定字段通过仍不证明SAR结构组装、整篇语义或其他图表正确。

`python -m scripts.export_phase5_review` 导出本地人工审阅页，原始批量记录和不同重验记录分开展示，并保留原图、回绘、失败原因、L1/L2和完整JSON。

## 全类型分子交付更新

详见[molecular-delivery.md](molecular-delivery.md)。所有路由均包含检测/识别/编号及universal回填；Report分别保留回填前/后结果与四类图像资产。初始结构化模型不再获取识别SMILES；可信Skill内部Tool返回完整数据。内部处理状态与独立化学内容验收分开。

## 单图路线中的编号系列（2026-11-21）

`scheme_extract` 0.6.0的反应端点可带 `compound_series`：`members` 为编号字符串集合，`member_count`仅计标签数量，`variable_definitions`保留图中变量选项原文，`structure_status`为unresolved/partial/resolved，另带缺失原因与证据。通用图示使用`scaffold_mol_id`，`mol_id`用于具体分子；范围不能作为单个`CompoundInstance`。缺少逐编号取代和位置信息时`variants=[]`，整体`status=unclear`；仅有部分证据时只输出对应实例，不生成笛卡尔积。numeric范围可展开编号，但不能据此枚举或分配结构。

`pathways[].steps[].relationship_type`区分reaction、optimization、selection、unresolved；现有旧结果缺省仍按reaction兼容。新模型必须依据当前图/图注证据显式判定，不将无条件箭头自动视为反应或优化。后处理对未解系列的reaction只保留错误，不生成具体反应事实；有证据的optimization/selection分别保存optimizes_to/selects_member，不代表逐成员具体反应。尚未确定的具体结构不是回填异常，仍需补证据，不伪造化学实例。

单图不假设拥有其他Table或Scheme。保留有用的集合、母核、具体绘图与箭头信息；分子框绑定覆盖完成与系列成员结构已解析是两回事。历史报告不自动升级或改写，需要创建新运行。旧运行的继承受材料/源码一致性检查约束；本次也更新了共享语义与回填代码，旧快照可能要求从类型判断重跑，不能绕过检查直接沿用。

### 反应物/产物中的Markush展开（scheme_extract 0.7.0）

每个通式端点在`reactants[]`或`products[]`内部增加`markush`，与`scaffold_mol_id`及编号范围并列：`description`解释母核与变量位置；`variable_options`逐变量列出可见候选；`substitution_cases`逐项保存原`expression`、展开的`group_text`、等价`assignments`、printed/symbolic_substitution来源及证据；另有`expansion_status`与`notes`记录覆盖和未知信息。允许局部符号取代展开，不需要Table提供逐编号结构。`compound_series`继续保存编号集合；`variants`只用于已知具体编号实例，两类展开不能混用。

例如–XCHYZ且X=O/S、Y/Z=H/F时，可在该端点内列出6类局部基团及8组赋值。它们不与1–10自动配号。同名变量在不同端点独立解释；未定义Alk等保留文字和partial状态。不向候选方案生成分子结构、分子式、化合物事实或反应配对。

## 单图可交付结果文件（2026-11-21）

正常FigureAgent完成报告后，及工作台完成post/report时，将backfilled_extraction独立保存为application/json Blob；工作台同时原子写入runs/{run_id}/final-result.json。报告与运行元数据的result_file记录blob_ref、下载文件名、schema_version、processing_stage和status。结果文件契约figure-result/1.0：来源document_id/node_ref/run_id/report_id、image_type、processing_stage、status、extraction（完整回填内容）与quality（检查/复核/覆盖及未知问题）；不输出完整调试状态或模型日志。post版本为backfilled/pending_review，report版本为reported及实际报告状态。

GET /documents/{document_id}/figure-reports/{report_id}/result、GET /documents/{document_id}/workbench/runs/{run_id}/result返回独立结果；download=true添加附件文件名。均校验文献归属，未回填返回409，不以回填前内容伪装最终结果。历史报告/已结束工作台运行可从已有快照补存文件，不调用模型；新运行独立命名、Blob内容不可变，旧版本不被新运行覆盖。

### Skill补充取证协议

单图仍由一个独立FigureAgent处理，选用对应Skill。本次scheme_extract 0.10.0新增可选根information_gaps，旧输出无需迁移。每项包含gap_id、question、field_paths（具体JSON pointer）、category（missing_evidence/conflicting_evidence/unsupported_representation）、next_action（search_text/inspect_visual/review_structure/manual_review），以及query_terms/source_hints/evidence_ids数组。存在缺口的输出必须status=unclear。

Agent自动将报告登记为skill:<gap_id>，保留当前图日志、基线字段与证据应用记录。extract_figure同时返回extraction和gaps；取证工具权限与文档范围不变。已有节点/页码只能来自真实材料或工具结果，source_hints不是读取证明。

finish之前逐项处置Skill缺口，不能用任意一次全局查阅代替所有问题。无法解决时update_gap必须保留unresolved/conflict、原因及实际已应用证据ID；纯结构表示能力限制可注明原因交复核。删除报告不等于关闭日志缺口，只有证据应用及相应校验后才允许关闭。最终needs_review及逐项未解原因保留在原有exploration字段中。

### 当前图分子拼接工具（策略4.3）

FigureAgent原生工具新增inspect_assembly、try_assembly、apply_assembly，不直接开放接收任意E-SMILES的底层工具。

| 工具 | 输入与行为 |
| --- | --- |
| inspect_assembly | 可选group_queries（最多16个）。返回当前图连接点摘要、有限等价简称、支持策略及位置参数限制，不披露识别化学串。 |
| try_assembly | items数组每批1–8项：instance_path、可选substituents、position_definitions、evidence_ids、note。仅试拼当前已存在的产物variants或compound_instances，返回逐项attempt_id/status/error/recovery_hint、计算结构与来源，不修改原提取。 |
| apply_assembly | attempt_ids每批1–8个。仅应用成功且未过期的尝试；整批先检查再写入，只更新ID-only实例赋值，不直接写计算产物。应用后必须validate_result。 |

当前每图最多32次本地试拼，另受既有Agent总工具/轮数/时间预算约束；每次试拼30秒超时，超时后的工作线程只操作隔离副本，不修改运行状态。失败项保留工具日志；成功应用也保留尝试、原字段、证据和来源指纹，当前图结构或实例变化会使旧尝试失效。

语义赋值/位置参数改变须提供当前已应用的文字或高清证据ID及说明；预览、未知ID或其他图分子不能作为结构输入。等价简称仅执行封闭映射，不猜Alk、不去掉位置前缀。已知表示缺口时，只改简称不能绕过位置检查。trial请求ok不代表各项成功，必须检查逐项status。

拼接和最终回填共享assemble_explicit及原有E-SMILES片段处理。显式原子替换优先；受严格结构检查的位置处理可移除指定的末端占位符并重映射剩余标签，再进入已有位置适配器，保留真实母核原子。位置参数当前仅支持已验证联苯醛的CHO position、XCHYZ position、X、Y, Z；带R/X/Y的兼容双芳环可按明确位置前缀处理氯取代。它们不是任意骨架编辑能力；不满足前置条件继续复核。

实例新增可选position_definitions，旧输入默认空字典兼容。视觉Skill仍输出ID-only数据，不输出计算结构或宣称工具已执行；scheme_extract 0.10.1、mol_backfill 0.5.2。已有JSON显示和工具事件可查看试拼输入、失败、应用记录，结果仍经正常回填独立保存。

### 通用关联图文补充与来源约束（策略5.0）

此机制不指定任何来源图名、论文编号或固定查询顺序。find_reference_visuals按实际缺少的成员编号/术语，对当前文献真实图表的图注、标签做候选定位；候选分数仅用于导航，Agent仍须结合正文引用和实际高清查看判断相关性。

确认其他图表包含当前产物的有效赋值后，可调用record_reference_assignments：输入product_path、实际高清evidence_ids、relevance及逐成员assignments（reference/substituents/position_definitions/evidence_text）。只允许当前产物已声明成员，母核ID沿用当前图；记录来源节点/页码、相关性和补充内容，独立于本图variable_options保存。检测ID空间仍不跨图混用。

每次重提取携带reference_substituent_evidence。宿主补齐已经确认但模型遗漏的字段，不静默覆盖冲突；目标端点、参考编号或母核改变会使原对应失效。回填前按实际来源约束检查，失败实例在分子计算/事实持久化前被blocked_instance_paths阻断；最终错误、缺口、来源仍留在报告中。修复恢复一致后解除阻断，历史错误不永久锁死实例。

不同成员若复制同一模板赋值而无逐成员依据，整个存在维度折叠风险的系列需进一步确认；它不直接证明不同编号必须对应不同化学图。经实际证据确认相同结构仍可记录对应。对于可修复实例，Agent不能从未试过拼接工具就直接结束；仍受调用和时间预算约束。

position_definitions是开放的来源维度记录，未知定义不能因当前适配器不支持而被删除。具体化学适配器仍有明确支持范围，未支持的连接、位置或基团保留待复核；此版本不承诺任意新骨架的自动组装。默认探索预算调整为32轮/40次工具，900秒不变；来源补充最多16批、每批32个成员。


### 结构化状态修复与内容保留（2026-11-22）

真实运行wd_01M818C5THBNHB6TVSVJHSDD8P在第三次提取时，模型先返回完整pathways，但根status=ok与未解compound_series不一致。网关要求重写JSON后，模型只修状态并将pathways清空，空数组通过包Schema；后续引用约束回填遍历已丢失的路径，触发IndexError。该运行失败，未产生可用最终结果，诊断见samples/outputs/figure2-reference-agent-rerun-validation.json。

scheme_extract 0.11.1新增保守状态修复：仅当语义校验全部错误都是总状态与未解成员/缺口不一致时，完整复制原始内容，将ok降为unclear，记录validation_adjustments并重新严格校验；其他字段/化学错误仍失败，不能降级掩盖。避免为已知的派生状态错误重新生成整份JSON。

Agent重提取采用事务保护：已有非空pathways、optimization_steps、rows或compound_instances变为空时拒绝替换，恢复原提取及引用证据应用状态，向Agent报告extraction_content_lost，失败计入尝试预算。该保护不是化学真值认证，不自动补造缺失内容。引用回填遇到已消失目标保留reference_target_changed与needs_review，跳过不存在的路径，避免越界崩溃。

本地87项回归通过，包含实际失败响应完整性复现、单请求状态修复、其他语义错误保持失败、空提取回滚、跨图来源约束和上游复用安全。真实模型重试使用新快照，结果另记。


### 前置独立结构审查与局部补丁（2026-11-22，策略6.0）

用户进一步明确：先把合成路线图的结构及当前图声明的全部产物通式可能情况找全，再执行确定性回填/拼接。提取后发现问题，不全量重新生成JSON。scheme_extract升级0.12.0；新增structure_audit 0.1.0。

实际流程为：复用检测/识别/编号图 → 首次ID-only提取 → 独立结构审查 → FigureAgent按需查阅图文 → 对错误字段或缺项应用局部补丁 → 独立复审 → 固定函数回填/拼接。实际组装失败后才由Agent介入补证或修正。反应物继续只保留通式，产物展开当前图声明的可能情况；这些可能情况与参考资料确认的具体编号成员分离。当前图没有逐编号对应时保留未知，不能要求审查者编造。

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

### 流程图精简交付（2026-11-22）

最终结果查看与下载接口对含 `pathways` 的结果执行固定字段投影，输出
`flowchart-result/1.0`：顶层只保留 `schema_version`、`status`、`pathways`。
每一步保留顺序、关系类型、反应物、产物、条件及必要的成员对应关系。
分子保留论文编号、名称、取代基、`attachments`、已成功拼接的 `smiles`；
通式保留母核 E-SMILES、变量选项及产物的取代情况、编号成员和未编号候选。
反应物保持通式，不展开成员。没有条件时为 `null`，失败拼接不输出成功 SMILES。
母核 E-SMILES 中的原子标签用于连接变量，属于化学结构信息，保留；PDF 坐标、
检测编号、位置参数、证据轨迹、审查和拼接内部参数均不进入精简交付。

此步骤不调用模型、不修改结构化/回填输入。完整调试文件 `final-result.json`
和各阶段状态继续保留；新运行同时写 `delivery-result.json`，存储元数据增加
`delivery_blob_ref`、`delivery_schema_version`。原有 `blob_ref` 仍指向完整版本。
历史结果在查看/下载时即时转换，无需重跑模型。其他类型图表的交付格式保持原状。
前端使用代码编辑器式 JSON 查看精简文件；完整调试信息继续在阶段面板查看。

### 按识别结构选择拼接能力（2026-11-23）

`scheme_extract 0.14.0`、`mol_backfill 0.8.0`、`structure_audit 0.3.0` 使用通用规则，
不要求固定的论文、图号、变量名、基团或成员数量。普通末端连接按识别标签处理；
`attachments` 仍是唯一参与拼接的字段清单。共享缩写库位于 `chem/groups.py`，
常规连接、候选生成和环位点适配共同使用，图类代码不再维护各自的案例基团表。

位置/拓扑能力通过 `inspect_assembly.capabilities` 披露，`position_contract` 不再
将历史 CHO/XCHYZ 字段组当作全局必填项。`assembly_compat.py` 隔离历史位置字段
规范化及专门的联苯醛位移处理，原始来源定义始终保留；其他未支持的编号体系仍报复核，
不能为适配工具删除、改名或猜测位置。

环位点适配根据图中末端/芳环连接点的拓扑判定内部作用，不依赖源标签叫 R/X/Y。
固定侧链不再要求 OCH2F，支持原识别骨架上唯一邻位固定侧链确定方向的有界情况，
显式卤素位点支持 F/Cl/Br/I。此适配仅覆盖两独立六元芳环、连接单键、可确定编号方向
及当前实现的位点作用；不是任意环系的重定位器。无法唯一确定时返回失败。
芳环原子替换扩展为 CH/N/NH/O/S，仍由价态与芳香性检查决定能否形成合法产物。

提示词仅保留一般性字段示例，内部变量与整体基团、候选与实际编号成员分开；
按每条箭头判定反应/优化/筛选，不能按整图主题套标签。独立审查提示同步检查
系列状态与后补证据的一致性。此改动没有自动重写历史任务及其状态。

验证包含任意标签改名、直链/小环/杂芳环、多个连接点、不同固定侧链和卤素、
芳环 O/S/NH 替换以及歧义/芳香性失败；另从已有识别与成员赋值确定性重放，
原案例25个成员全部成功且SMILES保持一致。合成夹具和单元测试验证有界能力，
仍需更多独立真实论文验证完整视觉提取效果。

### 逐步过程记录契约（2026-11-23）

探索 context 事件记录实际完成上下文裁剪后的 image_refs 与当前 review_id，
每张图以 blob_ref/mime 引用资产，并尽可能保留目标或参考角色、节点及页码。
structure_review 事件同时保存到探索 steps 与外部事件流，含审查输入 image_refs、
turn 和 parent_call_id；后者关联实际触发审查的工具。结构未变化的缓存审查使用
structure_review_cached/reused，不能计为新增模型请求。结构复用事件先于其触发的审查。
前端按操作顺序回放，模型请求图片清单和工具返回图片分开呈现；缺失元数据保持缺失，
不从后续查阅推测先前输入，也不把继承资料视为本次新调用。

### 结构化结果局部编辑工具（2026-11-23）

直接依赖已锁定的开源 `jsonpatch`（当前1.33）与 `jsonpointer`（当前3.1.1），
使用RFC 6902应用引擎和RFC 6901路径解析，不维护自写的JSON变更执行器。
上游资料：[python-json-patch](https://github.com/stefankoegl/python-json-patch)、
[python-json-pointer](https://github.com/stefankoegl/python-json-pointer)。

- `get_structure(path="", offset=0, limit=10)`：读取当前图当前提取版本的片段，返回
  extraction_version、value_hash及可寻址children。单次最多20个子项；大内容明确
  value_omitted，按子路径继续读或分页，不能将省略当为空。根路径为空字符串，键中的
  `/`写为`~1`、`~`写为`~0`。不提供任意文件或跨图写入。
- `preview_structure_patch`：参数与patch_structure相同，在隔离副本执行全部补丁、
  模板和证据约束，返回dry_run与预计changes；原结果、版本、审核及预算保持不变。
  它仍消耗正常的工具调用/对话预算，不能无限预检。
- `patch_structure`：继续接受expected_version、operations、evidence_ids、issue_ids和note。
  支持add/replace/remove/test；test核对旧值，不记为修改。保留局部编辑限制，不开放
  move/copy、根替换、已有对象/集合整体替换。数组追加使用`/-`，删除成员须关联审查问题。
  操作按顺序在候选副本执行，任一操作或最终校验失败均不提交。提交记录字段前后值及
  整体哈希，版本递增并触发独立复审；通过后仍须validate_result重做回填/拼接。

Agent提示和scheme_extract 0.15.0约定：查片段→按证据构造补丁→复杂修改可预检→
提交→复审，不重新生成整份结果。版本冲突/test失败时重新读片段，不盲目重试。
前端逐步记录展示查询、预检和实际应用，预检差异标记“尚未应用”，不冒充完成修改。

### 整体错误的受控结构重建（2026-11-23）

默认仍使用get_structure/preview_structure_patch/patch_structure局部修正。新工具
`regenerate_structure(expected_version, damage_kind, issue_ids, evidence_ids, note)`
仅针对已有scheme_extract结果；独立审查必须针对当前哈希返回needs_revision，并有
错误级别的对应问题。拓扑重建关联incorrect_topology；严重步骤遗漏还要求topology
覆盖项失败；多处占位符错误要求至少两个不同字段；模板不匹配要求other问题及
拓扑覆盖项失败。普通成员遗漏、未知映射、缺证据或拼接能力不足不满足条件。

默认figure_exploration_max_regenerations=1，允许配置0关闭、最多2；失败也计尝试。
同时受既有提取总次数、探索时间/工具和独立审查预算限制，不能耗尽审查预算再重建。
恢复同一结果时沿用structure_regenerations和structure_versions，不能通过恢复清零。
extract_figure继续仅用于首次提取；重建工具可发现，由执行器检查条件，而不是无条件
开放首次提取。模型输入的regeneration_context包含审核问题及重建原因。

仅调用_structure_once，不重跑布局、检测或识别。候选在副本生成并通过原Schema及
语义校验；模型异常、空结果、无变化、成员来源冲突时保留旧结构/版本/审核，已花费
调用仍记账。有效候选提交为新版本，旧结构/审核/已应用证据存structure_versions，
当前旧回填/拼接/检查缓存失效，强制独立复审；未通过时不能进入回填/拼接。
保留原证据与已确认成员赋值；拓扑变化时只能按相同原文指代与母核ID唯一迁移
成员来源路径，零个或多个匹配均失败，不按旧数组位置猜测。

前端在对应操作显示重建理由、新旧版本及嵌套复审；历史版本通过代码式JSON展开。
无自动真实模型重跑，不修改历史任务的状态。scheme_extract版本更新为0.16.0。

### 通用探索闭环与预算收尾（2026-11-23）

旧真实恢复运行32次工具调用中，5次尝试finish、7次update_gap，最终没有成员写入。
根因不是候选页不够，而是宿主未追踪失败提案、将备注成功视为进展、恢复图像仅有引用，
以及无收尾阶段。下面的机制不使用文献名、图号、页码或化合物编号特例。

1. 每次请求附带临时workflow状态：待修正/待应用编辑、按真实成员集合计算的未写入
   成员、剩余轮次/调用/审查/时间。它不是文献证据，不自动确认模型在备注中的事实。
2. preview_reference_assignments复用正式登记/应用函数在副本预检，返回字段差异；
   record_reference_assignments原子登记并应用，通过后复审。失败不留下半条来源记录。
   已选连接点缺键时返回repair.code/path/missing_keys/action；不代替模型推测化学基团。
3. pending_edits保留未提交提案、原参数和可修正错误。预检通过是ready_to_apply，
   不能直接finish。成功写入或确实匹配的局部补丁才解除待办；无关字段修改不能清除。
   重复相同成员登记幂等，不增加版本或消耗新审核。成员来源批次与局部拓扑补丁分别计限。
4. defer_edit可明确撤销错误提案，或纠错尝试后登记needs_evidence/unsupported；
   后两种至少有两次提案/纠错记录。所有提案与原因保留，缺证据/不支持只交needs_review，
   不当作已应用事实。更新已有gap可省略不可变question/field_paths，新建仍必填。
5. 恢复时从当前用户资产存储重新附上预算内的已保存高清图，优先失败提案引用的图，
   去重并检查文献/revision；遗漏及不可用清单明确记录。evidence_restored事件不冒充
   新查阅。其余图仍可select_context加载，不能凭旧标签推断像素内容。
6. 进展由结构、去重证据、真实校验和通过预检的提案变化判定；改写备注、重复校验
   或重复相同预检不重置计数。默认6次无进展调用停止并交复核，可配置3–20。
   参数错误最多获得2次有限纠错窗口，不算语义进展，不扩展总轮次/工具/时间预算。
   finish被拒后，在状态未推进前不再向模型提供该入口；字段纠错/待应用期间暂停宽泛发现，
   仍允许读取已知来源。明确延期可解除结束入口阻塞，但不重置无进展计数。
7. 默认最后3轮/3个工具调用，以及接近时间上限时优先收尾，暂停扩大检索。
   工具、时间、审查限制仍然存在；停止时保存unfinished_work，不伪造成功结果。
8. 较早且超过6KB的工具返回在模型请求副本中归档成元数据，保留最近6次完整返回、
   tool配对和Responses reasoning项；完整事件不变，可查询字段和重载图像。

前端按原操作链显示恢复图像、修正要求、workflow预算及未完成交接，原始错误仍保留。
Skill方案更新为scheme_extract 0.17.0；以上不修改识别母核、不自动拼接未知成员，
也不将“读过更多页面”作为解析成功标准。

新成员预检/提交的工具Schema要求显式attachments；历史记录仍兼容读取。
缺字段在预检时返回准确路径，不让登记成功后才在审查阶段发现。结构JSON的路径
只使用get_structure读取，文献get_node/get_children只接收真实文献节点ID。
每批补丁最多20个操作；单字段的标量列表允许replace，对象集合继续要求逐项修改。
大提案拆批后，只有所有具体字段均与提案一致才解除旧待办；追加下标不明或路径错误
仍需明确撤销/修正。defer_edit的业务reason必须保留，不能当作通用行动说明过滤掉。

### 真实整图回归后的来源核对（2026-11-23）

`reference_assignment_audit`在跨图成员提交前独立对照实际来源图核对提案。输入仅含
当前图、显式选中的来源图、编号成员赋值和本图位点清单，不共享FigureAgent对话。
逐成员检查基团、环原子、氮上取代、位置及其他结构身份列；不计算SMILES，
也不自动替换模型提案。输出assignment_index、field、可见来源依据及局部修改建议。
来源复核未通过时事务不提交，具体问题进入工具repair反馈；预检仍是无模型的格式/
应用检查，预检通过不等于来源复核通过。单次运行最多6次来源复核，失败也计次，
相同提案与图像命中已有结论，不增加请求；原来的总时间、轮次和工具上限不增加。
进程保留reference_review事件、父调用和实际输入图，前端在对应提交步骤内部展示。

登记前按当前识别母核检查完整attachments，包括环原子替换与氢取代位点。
完整的已登记成员赋值及全部位点齐全时，宿主同步系列structure_status=resolved，
此状态只表示成员赋值完整，不等于分子拼接成功。来源复核仍是正式提交的前置条件。
旧的开放通式候选与具体成员不混为一谈；不得为消除未解候选而伪造无限变量的穷举。

独立当前图审查增加host_field_checks，明确展示已有attachments，避免将存在字段
误报缺失；字段存在性、类型与位点集合由宿主确定性检查，模型核对图像语义。
Schema错误返回精确字段路径、约束和枚举允许值，避免长错误文本掩盖真正原因。
get_structure支持可选expected_version读前提。中文JSON材料直接使用可读字符，
避免Unicode转义使提示词膨胀，保留JSON往返与转义规则。原图及编号图的page按
实际分段region记录，不再固定为0。scheme_extract为0.18.0，structure_audit为0.4.0。

来源审核使用独立任务figure_reference_review，保留最大2400边长及中等推理，避免
细小位号在通用审查的缩图中损失。reference_assignment_audit为0.3.0，只审核本批
提交的成员，不要求一次交齐整张表；位号与撇号必须按实际图片逐字符核对。
模型审核仍可能误读，不能把审核通过当作人工核对或化学一致性校验的替代。

已登记成员可在record_reference_assignments中传supersedes（旧supplement_id列表）
及correction_reason，进行明确的来源纠错。只替换指定记录中与本次提案重叠的成员，
保留其他成员和识别母核。原赋值、来源及修改原因进入reference_assignment_revisions，
局部JSON补丁记录真实前后值；预检和审核失败均不提交修订，正式来源审核通过才提交。
恢复执行继续保留修订历史。普通登记仍拒绝冲突，不能通过静默覆盖消除证据约束。

拼接位置适配器只剥离封闭的中英文环位置描述，保留明确数字和prime，不从自由
文本中猜位置；源position_definitions不改写，计算投影单独规范化。此适配不扩展
任意骨架的编号能力，也不将开放Alk强制解释为某一种具体烷基。

显式卤素位号可在基团值中合写，或在同一位点的position_definitions中单独填写；
计算投影只在封闭位号和基团完全一致时合并，原字段保留。撇号矛盾、自由描述及
未知编号系统继续失败，不从句子中随意截取数字。
单成员try_assembly只对当前母核和该成员引用的本图片段做覆盖检查，避免其他分子
未参与本次试拼产生无关错误；完整图的覆盖检查保留。来源冲突反馈给出正式修订入口。
缺口检查展开C_BACKFILL_ERR对应的逐字段错误，不让已定位的其他成员错误通过
空路径汇总重复阻塞无关的已验证缺口；真正无路径错误和本字段错误仍阻塞关闭。
workflow返回可修订来源记录索引、未解决缺口及审核问题。审核未通过时不提供试拼/
回填工具，已有缓存审核结论不重复作为可调用审核；恢复任务先校验取得当前错误，
避免重复通读整份既有结果。所有原调用、拒绝、修订和参考图仍保留在过程记录中。

structure_audit为0.5.0，另附host_field_checks.topology，将每条步骤的路径及直接
reactants/products端点单独列出；嵌套variants不作为另一条箭头，语义问题必须定位
到实际相符的端点。系列端点的字段校验同时说明reference、compound_series、
markush必须怎样共存，避免Agent错误地来回删除依赖字段；反应物通式不必展开。
已在本图单独绘制并识别的系列成员保留直接结构，不由来源赋值工具覆盖成待拼接
实例。工具提示仅提交其他成员；在当前图识别有效且占位符通过审查时，直接成员与
有来源的拼接成员共同计算系列完整性。此完整性仍不替代分子有效性和拼接校验。

旧运行中若已把直接绘制成员登记为母核拼接约束，恢复时仅在本图对应识别有效时
把该条登记保留为observation_only来源观察，不让它覆盖直接结构；其他成员的来源
约束不变。迁移在副本上应用局部补丁并校验，保留原赋值和来源、记录迁移事件，
之后仍需新的独立审核。重复恢复不会重复迁移。

恢复运行的patch_budget_baseline只用于从历史补丁总数中扣除恢复前记录。
MAX_PATCHES保持5，不增加本轮模型/时间/工具预算，旧补丁和拒绝记录仍全部保留。
开放通式候选失败定位到markush.substitution_cases，避免范围错误阻塞其实际成员。

收尾检查点：独立审核通过、当前版本已校验、没有待提交编辑，且每个缺口已有实际
来源及明确处置后，检查逐问题来源查阅要求。满足时进入submit_reviewed_result，
本轮只提供finish，明确提交摘要和未解决事项，避免继续重复能力检查和导航。
这不改变finish的证据门槛，也不将needs_review伪装为completed：实际成员失败、
无路径校验错误及未处置缺口继续修复；只有已记录出处和限制的开放通式候选可以
作为需复核事项交接。每个缺口首次有据处置只计一次收尾进展，重复备注、状态来回
切换不续预算。工作清单另列当前可用于编辑的证据ID，避免复用未选中证据。
structure_audit 0.5.1在任务首尾展示完整箭头索引，要求逐路径核对包括末条步骤，
防止把嵌套成员误作步骤或把已存在末条箭头误报为缺失。

### 报告后的精简过程交付

报告后增加deliver（精简过程结果），普通FigureAgent与工作台共用固定函数
summarize_result（drugpaper/facts/flowchart_delivery.py）。只做确定性字段投影，不调用
LLM、不重写化学事实。输出flowchart-summary/1.1，保持原有
pathways/steps/reactants/products/variants层级，反应物和产物直接内联对象，不生成
m1/m2引用或额外molecules字典。保留论文reference、名称、通式、必要取代信息、
已确认SMILES、步骤关系、条件；产品variants保留全部实际编号成员。未解决成员
仍保留，smiles=null并标注状态。反应物通式不新增展开。

最终短版不包含candidates，理论组合仅存在中间文件，不与已确认成员混合。
字段白名单排除PDF坐标、检测编号、证据长文和调试记录；position_definitions是
影响化合物身份的化学位号，必须保留，不能误当PDF坐标删除。通式母核连接标记也
属于化学表达，继续保留。原详细文件不修改，顶层质量状态不因精简而变成成功。

工作台summary-result.json是短版；delivery-result.json保留完整化学成员和候选；
final-result.json保留详细中间报告与审查记录。Blob元数据新增summary_blob_ref。
两种结果API默认view=summary，view=delivery/full可单独下载明细/完整中间文件。
严格的阶段边界：post仅保存完整backfilled/pending_review回填预览，不写入精简
Blob或delivery-result.json/summary-result.json；此时结果API各view均返回完整预览，
前端标为“回填预览（待检查）”。报告完成后才允许生成精简副本，deliver作为正式
短版步骤保留。summarize_result对backfilled输入也只返回完整副本，避免绕过阶段。
旧报告访问时也能确定性生成短版，无需重跑模型；仅从deliver开始是整理指定历史
报告，不表示用新代码重验其化学事实。其源报告不重复插入数据库。
适用于所有受支持的合成/优化流程图，不绑定论文编号、图名或取代基；其他图像类型
继续沿用已有交付结构，避免将表格/谱图强制改成路线图。

路线解析Skill契约同步（2026-11-24）：scheme_extract 0.18.1在SKILL.md、主任务和
molecular-workflow.md明确完整ID-only提取与宿主deliver的职责边界；不新增摘要LLM
调用、不改提取Schema。后续所有本Skill支持的图像自动沿用报告后的固定投影，
不绑定具体图表；审核者不能把最终文件约定误当当前ID-only输出需要SMILES。

### 点位策略、按需审查与流程图先行（2026-11-25）

结构化模板仍是ID-only：只写当前图`mol_id`/`scaffold_mol_id`、论文编号、局部变量、
点位和证据，不写识别SMILES、分子式或拼接结构。`position_definitions`保持可选；
新增两层策略，避免“文献没有点位”被当成永久错误：

- 端点/系列级`position_policy`：`requirement`为required/optional/not_applicable/
  unknown/exhausted，`scope`为per_member/endpoint，`source`为printed_column/
  printed_prefix/cross_reference/none，另有source_ref、basis、evidence_text。
- 实例级`position_status`：provided/not_printed/unreadable/missing/not_applicable。
  provided要求`position_definitions`非空；not_printed/not_applicable/missing/
  unreadable不得与已给position_definitions并存。

审查host检查按策略放行：required且missing/unreadable才报error；not_printed、
not_applicable、exhausted不作为error；optional缺失为warning；unknown先按调查计划
取证并报warning，不直接阻塞。声明not_applicable但substituents、markush或
position_definitions中存在位置前缀，或跨图来源记录带位置时，仍报error。未知点位
不能用伪造not_applicable/exhausted绕过；达到最大努力取证后才允许exhausted。

结构审查改为按需触发：首次提取后审查一次；patch_structure、repair_markush_cases、
record_reference_assignments、apply_assembly不再自动复审。Agent累积修改后，在准备回填
前主动调用review_structure一次性复审；同一结构未改动时复用缓存。validate_result与
finish继续要求当前版本的审查通过，不能绕过门禁。

审查未通过时先交付ID-only流程图：`molecular_coverage`写
`status=pending_review`、`delivery=flowchart_only`、`reason=structure_review_not_passed`，
仍生成report/deliver结果文件；分子回填保持pending，不把整单结果伪装成已完成。

结构化阶段新增只读工具`inspect_attachment_sites`：只披露识别母核的连接点标签及
逐实例attachments校验（missing/unexpected/status），不披露化学串。attachments必须
恰好覆盖该母核的全部实际连接点；no_attachment_sites的母核不能作为拼接模板，应换用
Markush母核或记录unsupported_representation。创建/修改variants后应再次调用该工具核对。

兼容性：旧JSON没有position_policy/position_status时按legacy处理，不自动重写历史结果；
`scheme_extract 0.19.0`、`structure_audit 0.6.0`；common_extract、table_extract、
structures_extract等同步可选字段与补丁版本。真实模型新快照验收另记。
