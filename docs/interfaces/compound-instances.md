# 图表化合物实例契约

2026-10-03；适用于阶段 5 FigureAgent。本文区分程序契约验证与真实文献化学正确性验收。

## 图像、识别对象与化合物实例

一幅图内 mol_id 对应检测框，不等于论文中的化合物编号。对象 object_id 使用拥有文档/版本/图像归属的节点引用，不在不同用户或图像之间共享编号表。识别候选保留原始 E-SMILES、校验结果、结构候选 ID、框位置和分段编号。`mol.deliver` 输出 molecule_objects，失败对象也保留；角色按 E-SMILES 保守判定 scaffold/substituent/sru/unknown，普通 SMILES 不能独自证明视觉角色是完整分子。

VLM 看原图和编号图，只生成图像语义、mol_id、印刷标签、取代基定义和证据。识别 SMILES 不进入 figure_structure 或图像复核的材料。工具在视觉结构化之后解析识别结果、执行拼接，并把结果回填至原来实例的位置。回填前输出不被改写；后处理完整结构另存。

`CompoundInstance` 见 [JSON Schema](phase-5/CompoundInstance.schema.json)。实例具有 reference、非空 evidence_text，并且只能使用以下一种来源：

- `mol_id`：图中明确展示的完整分子；不再同时指定取代基。
- `scaffold_mol_id` 和 `substituents`：母核编号与逐个取代基定义。定义是编号整数或受支持的印刷缩写（H/Me/Cl 等），不能用模型生成的 SMILES。

SAR 使用 `compound_instances[]`，必须有 `row_index`，从当前返回表格的零起始数据行计数；标签必须实际出现在该行。大表分片合并时重算行号，重叠片段出现矛盾定义即失败。指标数据不进入取代基定义。SAR Skill 按 measurement evidence 的行路径连接指标，不按标签联接，避免同号或重复数据行串行。

分子展示图可在根使用 compound_instances。反应产物在相应端点对象中使用 variants。10a–c 只是系列标签，不是三个化合物的定义：只有各成员的实际取代基定义可见，才输出三个实例。

```json
{
  "reference": "10a-c",
  "scaffold_mol_id": 0,
  "variants": [
    {"reference":"10a","scaffold_mol_id":0,"substituents":{"R":1},"evidence_text":"10a 的 R 是框1中的基团"},
    {"reference":"10b","scaffold_mol_id":0,"substituents":{"R":"Cl"},"evidence_text":"印刷定义 10b: R=Cl"},
    {"reference":"10c","scaffold_mol_id":0,"substituents":{"R":"H"},"evidence_text":"印刷定义 10c: R=H"}
  ]
}
```

## Skill 与 Tool 的执行职责

scheme_extract、table_extract、structures_extract 的 Schema 和提示词公开所需契约；仍由原有路由渐进加载对应 Skill。不向 Agent 一次公开所有化学细节。

mol_backfill 从受用户归属检查的材料加载框和结构，调用 mol.deliver。工具在线程中执行纯计算；每次调用的字典、分子对象和索引局部隔离，不使用全局“当前图像”。`instance_delivery.materialize` 使用通用 E-SMILES atom/ring attachment 拼接，保持 prime 标签独立，不使用针对测试文献的母核匹配分派。旧 SAR 接口中的窄母核兼容实现仍保留，但 FigureAgent 要求显式实例；缺失契约直接 needs_review，不能借兼容分支声明新链路完成。

成功实例包含 assembled_molecule（canonical_smiles、formula、InChIKey 等）、assembly_provenance（母核/取代基绑定、字段路径、证据、方法）和 assembly_status。SMILES 是结构表示，formula 是分子组成式，二者分别保存。mol_backfill 把完整实例保存为独立 StructureCandidate 和 LabelClaim；母核和基团仍保留原识别节点，不给基团错误地贴上产物标签。由此后续 compound_resolve、文档内结构查询和知识问答可以读取真实结构事实，而不是只读取展示文件。

sar_record 附加各行 properties；原始 compound_instances 同时得到该行指标，完整结构和来源保留。每个非空数据行都必须交付实例，缺行通过 missing_row_indices 暴露。

流程图使用 `pathways[].steps[].reactants/products` 保存反应端点。系列展开保留在原端点；scheme_relations 从最终回填结果投影反应事实和 product_structure_id。源端也是系列时必须有 variant_links 的可见配对证据，不能做系列笛卡尔积。多个标签共用同一个 mol_id 时也视作系列，不能用端点列表绕过检查。旧 optimization_steps 链仍可读取；复杂平行关系使用显式 reactants/products 契约。

## 失败与验收边界

未知/重复 mol_id、识别失败、缺少定义、没有模板变量的赋值、非法缩写、嵌套未解模板、多个拼接产物、残余 dummy、化合物行错配和无证据的系列配对都不能生成完整结果。原编号和错误保留，FigureReport 为 needs_review。工具计算成功和 Skill 执行完成不等于文献内容准确。

通用拼接要求识别的 E-SMILES 提供可信连接位点；位置含糊、仅文字描述但不能映射到位点、视觉遗漏或识别错误仍需复核，不能通过枚举后随便选一个产物掩盖。当前实现不是对任意 Markush 图像准确性的保证。识别到的基团必须有明确 dummy 连接点，母核 atom 标签不能移到其他原子；通用实例路径拒绝自动修补位置。显式 (R)/(S)-编号且图只有一个可判定手性中心时，可由代码按 CIP 推导实例，来源标记 stereochemistry_from_explicit_label；多中心或与识别手性矛盾则失败。该推导不等于从图像识别出了手性。流程图端点引用持久化的完整实例结构，保留 instance_source_path。

`tests/unit/figure/test_compound_instances.py` 使用未见母核测试真实 RDKit 图连通性、编号引用、取代基/prime、错误拒绝、受控识别响应经过 Skill 持久化，以及 FigureAgent 的 SAR 指标行绑定。实际文献运行原始请求/响应与输出单独保留；不修改历史结果或用测试期望注入模型输入。

## 局部语义失败的恢复

模型网关先校验 Schema 和箭头局部语义，正常执行一次结构修复重试。若仍失败，仅当原始响应完整满足包的结构 Schema、而可隔离错误是跨箭头配对时，运行时才保留所有分子/实例输入、将错误连线移入原步骤的 rejected_variant_links，并记录 semantic_errors 和原字段路径。随后再次执行完整语义校验，输出始终 status=unclear、needs_review=true。缺失/非法分子来源等其他错误不能走这一恢复路径。

回填代码可交付这些有效分子的结构；路线工具保留隔离错误，不把恢复视为内容通过。原始失败响应写入真实测试调用日志。`test_agent_recovers_valid_molecules_after_semantic_model_failure` 验证恢复实际经过 FigureAgent、Skill、化学工具与持久化链路，最终报告为 needs_review。

CIP 使用现代 rdCIPLabeler，并在最终标准化的 SMILES 图上复验；不能只看临时原子的旧 _CIPCode。硫中心测试涵盖 sulfoxide、联芳 sulfoxide 与 sulfoximine，真实保存的视觉输入重执行后，全部显式 R/S 与最终图一致。原算法错误的输出仍是历史失败，不作为通过证据。
