# P5 代码地图

版本0.5.0，2026-10-02。阶段开发项已实现；真实模型验收结果单独记录在 `samples/outputs/phase-5/20261002/completion.md`，不能用单元测试代替真实抽取正确率。

| 计划任务 | 实现 | 测试 |
|---|---|---|
| 5.1 指标词典/数值解析 | config/metric_dictionary.yaml；facts/value_parser.py、units.py、metric_dictionary.py | figure/test_values_and_tables.py：误差、科学计数、范围、限定、无值、次级Kd、模糊匹配和单位 |
| 5.2 表格/测定/SAR | skills/figure.py；table_extract、measurement_map、sar_record 包；facts/table_processing.py、table_transcription.py | test_values_and_tables.py、test_table_slicing.py、test_persistence_and_modes.py：Table3十一项、真实Table1人工转写十行与两类百分比终点、脚注、切片、原图和标注图同次请求、确认母核 |
| 5.3 路线/结构 | scheme_extract、structures_extract 包；skills/molecule.py 回填；facts/figure_projector.py | 注册回放、test_persistence_and_modes.py：事实投影、证据归属、回滚与历史停用 |
| 5.4 图表数字化 | chart_digitize 包；vision/chart_cv.py；skills/figure.py:chart_post | test_chart.py：20组不同数据的柱状/曲线/散点/对数轴、标定异常值；test_values_and_tables.py |
| 5.5 文本层/L1 | vision/text_layer.py；validators/figure/ 四维规则与codes.py | test_validation_and_disclosure.py：25条L1各正反例；真实PDF文本层；扫描空值 |
| 5.6 回绘/L2 | vision/overlay.py；validators/figure/checklist.py；figure_self_check 包 | 清单截断、unchecked、封闭核对、不确定阈值、万行材料裁剪；真实50→500受控自检 |
| 5.7 FigureAgent | agents/figure/graph.py、state.py、report.py、accounting.py | test_agent.py：正常恰好3次、定向修复、两轮失败预算、初次自检失败保留、缓存与用户归属 |
| 5.8 补救与渐进披露 | agents/figure/remediation.py；action_* 包；capabilities 路由；ToolCaller白名单 | test_validation_and_disclosure.py、test_agent.py：相关动作枚举、未披露拒绝、越界路径、人工锁、超时预算 |
| 5.9 Coordinator | agents/coordinator/blackboard.py、dispatch.py、reconcile.py | test_coordinator_and_pipeline.py：页序、同页结构优先、并发6；冲突问题分组/最多8；query只读 |
| 5.10 批量/恢复/限流 | orchestration/figure_pipeline.py；workers/；gateway/limiter.py；agent/checkpoint.py | test_api_and_worker.py、test_limiter_and_batch.py；integration/test_figure_resume.py、test_celery_recovery.py：PG跨连接、8路首次初始化、跨客户端Redis、真实Worker硬终止 |
| 5.11 领域尽力项 | docking/metabolic/protac/pharmacophore/common_extract 包 | 每包3项注册回放；结果保留unclear/status/evidence，复杂图人工复核 |
| 持久化/接口 | 0007_figure_agents；facts/figure_projector.py；api/batches.py、figures.py；查询Tool；CLI figures | API批次状态/派发失败、事实原子性/所有权、Panel坐标、只读query/review、迁移回归 |
| 冻结/部署 | docs/interfaces/figure-agents.md、phase-5/、openapi-p5.json；deploy/systemd/drugpaper-worker.service | Ruff、mypy、JUnit；本机API0.5.0/三服务健康；Docker仅提供模板 |

调用链：主图黑板v1 → Send/Coordinator信号量 → FigureAgent固定三步 → 按失败检查码披露动作卡 → 加载选中Skill及其白名单Tool → L1/L2验证 → 报告/事实事务提交 → 化合物归一与黑板v2 → 一轮只读定向query。

Skill固定流程只加载当前类型的结构化包；补救只展示相关2～4个能力卡与action_enum，不发送整个目录。Python Skill通过代码完成切片、后处理、CV、组装等工作。数值/单位保持原文、证据和状态；没有可靠母核时保留SAR行及assembly_skipped，避免把任意Markush片段当母核。

P6交接：测定、条件、路线/关系、FigureReport、未核验字段及定向问答。P7接HTTP用户认证与完整模型网关；P8接人工纠错及重跑；P9接沙箱run_code。SDK重试不是额外逻辑任务，统计不能直接当供应商账单；外部请求完成而提交前崩溃不能保证不重发。

## 内容验收补充（2026-10-03）

- scripts/audit_phase5_results.py、verify_phase5_tables.py：拒绝错误兜底、逐格比对、脚注与关联核验，保留未独立核验状态；tests/unit/figure/test_acceptance.py覆盖模型假阳性、错误数值/重复指标、prime/误差、错误浓度和脚注错位。
- facts/table_transcription.py、table_extract包：紧凑传输与稀疏元数据校验；test_values_and_tables.py验证完整TableGrid恢复、脚注、测定条件及4/24小时分别关联。
- validators/figure/checklist.py：即便所有数值有文本证据仍核对表头/脚注/覆盖；test_validation_and_disclosure.py验证新C_METRIC_UNMAPPED正反例。
- scripts/export_phase5_review.py：离线HTML人工审阅，独立显示历史失败和最新重验及其核验范围；真实证据保存在samples/outputs/phase-5/20261002/，不使用模型输出作为人工gold。

## 全图像分子回填更新

- facts/molecular_delivery.py、tools/builtin/chem.py:mol.deliver、skills/molecule.py:mol_backfill：递归绑定、严格候选与覆盖核验、白名单接入。
- config/routing.yaml、skills/prompt_materials.py、skills/execution.py：全部类型接入，SMILES与初始模型材料隔离，原图+编号图同次结构化。
- tools/registry.py、skills/bootstrap.py：内部调用完整返回，模型输出仍截断；避免大批量数据变成truncated_text。
- vision/molecular_delivery.py、agents/figure/{graph,report,state}.py：四种持久化图像，8分子分页与报告字段。
- tests/unit/figure/test_molecular_delivery.py：全形态、错误拒绝、大结果、多用户隔离与图像检查；集成恢复夹具增加空白PDF检测后端，恢复验证仍实测PG/Redis/Worker。
- scripts/verify_phase5_molecules.py、export_phase5_review.py：原图独立内容对照与可视化，历史失败不覆盖。

## 通用化合物实例与反应系列（2026-10-03）

- `facts/compound_instances.py`、`facts/figure_models.py`：ID-only CompoundInstance、反应端点与 VariantLink 语义；来源互斥、非空证据、箭头局部配对。导出 Schema 在 `docs/interfaces/phase-5/`。
- `chem/instance_delivery.py`、`facts/molecular_delivery.py`：确定性全实例回填、E-SMILES 通用附件拼接、严格位点、完整图校验；新路径不依赖两种测试文献母核分派。检测对象与完整实例分开保留。
- `skills/molecule.py`：受归属检查的材料读取、完整实例的结构/标签事实投影、文档内端点实例绑定；唯一中心的显式 R/S 推导有来源标记。
- `skills/figure.py`：分片表的实例行号合并、SAR 原行指标联接、显式反应端点/变体投影；重复检测框的多标签端点同样要求系列配对。
- `skills/execution.py`：scheme_extract 使用包 Schema 加 VisualExtraction 语义校验，让网关的结构修复流程能发现跨箭头错误引用。
- `agents/figure/graph.py`、`validators/figure/structure.py`：后回填路线投影、SAR 强制显式实例、精确错误路径、错误结果保留。报告缓存分子契约为 9。
- `tests/unit/figure/test_compound_instances.py`：未知母核、编号、prime、来源互斥、位点拒绝、SAR 重复行、真实 Skill/数据库链路、FigureAgent 数值绑定、R/S 和系列连线等运行验证。完整说明在 `docs/interfaces/compound-instances.md`。
- 真实运行发现了未指定手性被当成两种手性结构、路线系列笛卡尔积以及模型把下一步配对放到前一步的问题；原产物保留，另存独立审查与新运行，不改历史 JSON 来伪造验收。
- `facts/reaction_semantics.py`、`skills/execution.py`：结构 Schema 合法但跨箭头配对错误时，隔离原连线、保留分子和字段证据、强制 unclear；非法分子来源仍严格失败。恢复路径通过实际 FigureAgent 单测，报告缓存分子契约最终为 10。
- `facts/reference_delivery.py` 使用现代 CIP 并检查最终图，含3类硫中心回归。`scripts/recheck_phase5_instances.py` 对原始真实 ID-only 输出重执行生产 Skill，无手工改源数据；最终725单元/集成通过，详见阶段输出目录 compound-instances-completion.md。

## 图类 Skill 复用契约（2026-10-04）

- 九类视觉包及十个支撑包的五个 L1 小节包含本地资源入口；references/molecular-workflow.md 提供图类专属步骤、实例字段、确定性回填、失败与验收。原先额外小节不会进入 L1 的问题通过实际披露小节修正，未放宽预算。
- prompts/common/mol_instances.md 与七个 VisualExtraction 图类的 includes、领域 task.md，表格独立 task.md：关键约束进入实际模型输入，L3保持按需读取。mol_reference.md 与真实编号图先、原图后顺序对齐。
- tests/unit/skills/test_molecular_workflow_disclosure.py：35项真实元工具披露、资源完整性、九类产物示例和模型提示组装检查；复用入口 docs/interfaces/molecular-skill-reuse.md。检测覆盖、化学位点和手性的准确性仍须内容验收，不能由文档/单测推断。

- 固定最终包内容后完整回归760通过，Ruff/格式检查和mypy通过；报告 samples/outputs/phase-5/20261002/tests-skill-workflows-20261004.xml。初次回归期间编辑包目录触发快照变化保护，停止编辑后重新完整运行，此处只引用最终通过报告。

## 自主公开调用验收（2026-10-04）

- capabilities/{meta_tools,toolset}.py、tools/protocol.py：多关键词发现、加载卡片、精确包参数、完整产物ID交接、内容状态、产物类型约束。
- skills/{bootstrap,runtime,execution,loader,molecule}.py：自动材料与编号指纹、检测前置、当前结构指针、识别化学字段隔离、待复核状态、执行缓存契约和可恢复参数错误。
- agent/runtime.py与validators/workflow.py：独立轨迹产物ID与实际调用完成校验；共用ReAct自主选择工具/Skill，固定FigureAgent质量阶段保留。
- tests/unit/figure/test_autonomous_skill_tools.py与工具/能力/加载器回归：九类公开Skill、嵌套Tool、产物/用户隔离、局部识别交替、输入隔离、Agent提前结束恢复；scripts/verify_skill_autonomy.py执行真实自主Agent并独立核对最终产物。真实通过目录live-skill-autonomy-typed-handoff-20261004，失败历史独立保留。
