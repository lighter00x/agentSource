# 全类型分子回填与图像交付（2026-10-03）

## 统一链路

所有图像类型（路线、SAR/数据表、结构展示、图表/性质图、对接、PROTAC、代谢、药效团、混合Panel及其他图）均先运行 molecule_detect → molecule_recognize → som_render，再加载唯一的结构化Skill。GPU检测仍由原有共享Worker执行；应用不为用户重复加载YOLO模型。

结构化模型只见原图、编号图、框坐标及限定正文材料。project_target不再披露结构候选的SMILES；原图和编号图在同次请求内供所有结构化Skill使用。模型输出mol_id/scaffold_mol_id/mol_ids或取代基编号，不负责化学结构回填。

mol_backfill的`contract="universal"`通过白名单Tool `mol.deliver`递归解析显式分子引用，覆盖表头/单元格、系列、路线层级和任意领域实体。单元格数值与普通文本整数不会被误当作分子编号。原编号、字段路径、结构候选ID、节点、图像片段与框位置保留；E-SMILES逐字取自该节点唯一有效识别候选。原始对象保持不变。

旧十类流水线的legacy契约保留，供兼容旧调用；FigureAgent的所有类型统一调用universal契约。Python后处理可以读取完整候选材料，初始结构化LLM不能读取它。

## 拦截与核验

不存在/布尔型编号、歧义候选、空识别、化学校验失败、模型自带SMILES、冲突排除以及任一未解决的检测框均产生明确错误，不用空字符串或Unclear替代可交付结构。识别器的valid标记还需通过实际化学图解析复核。

每个检测框必须有显式绑定，或提供可在原图核对的非化学排除理由。回填后由代码依据已有字段引用生成逐框inventory，不凭空生成标签或遗漏引用。L2核对原图是否漏检、编号与字段对应、每个排除理由；无分子框的图像也必须检查漏检。发现预期含结构而零框时按error阻断。

`complete_for_detected_boxes`仅表示检测框绑定完整，绝不等于YOLO召回率100%，也不证明识别出的每条化学结构与原图完全一致。图像产物在人工/独立内容验收前标为pending_content_review。FigureReport的ok是内部处理状态，独立内容验收另行报告；RDKit有效性只证明结构能解析。

## 产物

FigureReport增加pre_backfill_extraction（回填输入）、backfilled_extraction、molecular_coverage和image_products；即使后续自检失败也保留已完成产物。

每个片段保存original、before_backfill（编号框）、after_backfill（逐框状态）、final（原图+分子裁剪+识别文本）。final每页最多8个分子，背景最长边1600px，顺序写入内容寻址资产；不一次构造巨幅联系人图。编号在同一图像跨片段唯一，不混用其他图像或用户的索引。JSON保存完整识别文本，图像明细文本仅用于快速查看。

另外保留实际模型编号输入（model_input_numbered）；即便结构化失败，也保存真实原图和编号输入，不创建假的回填结果。一个Skill Artifact引用全部回填图像，不要求多个Artifact破坏单Work Unit原子提交约定。报告和对应资产均来自同次任务；重验保留历史失败。needs_review报告允许再次尝试；新契约及包内容哈希使历史无产物报告不被当作新版完成结果。

## Tool与Skill内部调用

ToolRegistry.invoke的默认行为仍限制面向模型的输出。可信Skill内部调用使用truncate=False，保留参数验证、归属检查、白名单和超时约束，完整交付结构化返回值。解决默认4000字符截断造成长表/分子批量结果损坏的问题。

核心回填Tool无共享可变索引；20个并发用户相同mol_id仍使用各自请求的节点/结构数据。Skill的候选来自context_for的文档归属检查；Tool作为纯计算能力不主动读取其他用户数据。

## 证据与范围

scripts/verify_phase5_molecules.py在隔离库重验结构展示图、路线图及SAR表，保存before/after JSON、四类交付PNG及实际模型编号图；真实模型提示材料同时留档检查SMILES泄漏。tests/unit/figure/test_molecular_delivery.py覆盖十种嵌套数据形态、大返回值、无效结构/编号、歧义、排除、原文不变、图像有效性及多用户隔离。

真实结果目录：samples/outputs/phase-5/20261002/molecular-delivery-current（此前verified/final为上一轮成功证据）。Figure1独立参考覆盖两个分子的数量、标签、位置及化学连接关系；立体化学未独立认证。其他图像的完整化学语义、Markush扩展文字和整篇质量尚需独立核验，不能把JSON/PNG存在或模型自检当作通过。

真实脚本的整体未完整独立核验时返回非零退出码，即使有限字段或内部处理已通过，也不以退出码冒充完整内容验收。

当前最终回归635项单元/集成测试通过（124.18秒），JUnit保存在samples/outputs/phase-5/20261002/tests-molecular-delivery-final.xml。Ruff检查与457文件格式检查、mypy228源文件检查通过。Table1另对人工基准核对80/80数据单元格及脚注关联通过；这些验收范围不能扩展成整篇准确率。


## 结构化回填与SAR交付

回填的主要交付是结构化数据，不是框图。mol_id仍作为溯源编号保留，旁边增加esmiles和molecular_structure（原始识别文本、canonical_smiles、formula、is_markush）；母核对应scaffold_esmiles/scaffold_structure。含通配符或Markush候选不提供完整分子的分子式，避免模板被当作成品。

SAR交付附加sar_records：逐行保留拼接status/products、规范化完整结构与分子式，以及来自同一数据行的文献测定properties（值、误差、单位和来源）。相同化合物标签出现多行时按行证据关联，不能按标签混合性质。修复原sar_record用原始rows覆盖拼接rows、FigureAgent丢弃SAR返回的问题。未知或歧义取代基不使用Unclear进行拼接。未完成全部拼接的SAR报告为needs_review，模型自检不能将其认证为完成。

review/index.html默认展示每张图表最新的结构化回填数据和SAR完整分子/性质表，提供results/*.json下载；图片与历史审计保留在audit.html。当前真实Table1十行各保留三项测定，但位置变量CHO position/XCHYZ position尚不受拼接器支持，因此均明确skipped/positional_variable。常规R基团完整拼接及重复标签逐行关联已单元测试验证，不能据此声称该位置变量表已完成。


## 全图表交付补齐（2026-10-03）

真实验证脚本默认选择文献全部图表，而不是三张样例。当前文献17张图表均保存结构化输出，历史初版另存molecular-delivery-all-initial。含二维化学结构图均交付显式编号绑定；仅含曲线的图不需要生成分子结构。p009.b006的曲线被检测为分子仍保留误检/复核标记，未用伪结构填充。

拼接器补充两种有明确结构约束的模式：双苯环醛的CHO/XCHYZ位置（以联芳键为1/1′，反向等价位点去重）、带明确R端点/Y芳香CH或N/X取代位置的联芳模板。仅接受严格支持的拓扑、基团和位置，位置占用、多产物、未知基团均失败。两张真实SAR表分别10行/16行完整结构与独立书写的结构基准一致，保留26行各3项文献测定，共78项；不同表重复化合物3，因此26是行数，不是26个不同化合物。硫中心未指定构型的表格产物保持未指定。

新增mol.lookup_references：仅查询调用用户当前文档的活动LabelClaim/StructureCandidate，完整候选的规范化结构不唯一时拒绝。三维对接图按明确配体编号关联文档结构；(R)/(S)标签只在唯一可能手性中心时转成相应CIP构型，并明确标记stereochemistry_from_explicit_label。这是从明确名称与已知连接结构推导，不能称为直接从3D棒图识别。source_ref为原化学图来源，不能被自检替换为当前图注。完整引用3D实体无需虚构二维框；漏检的真正二维图仍需阻断。

回填现在先于性质/曲线处理，后续异常不能丢失已有结构结果。mol_backfill的缓存关闭，避免跨图文档结构库更新后复用旧编号查询。错误inventory类型返回明确失败；补救在采纳之前校验整体结构，拒绝列表被对象替代。最终694项单元/集成测试通过（128.46秒），Ruff/461文件格式与mypy231源文件通过。整篇内容、全部曲线参数及全部化学识别的独立正确性仍未认证，整体not_fully_verified保持。
