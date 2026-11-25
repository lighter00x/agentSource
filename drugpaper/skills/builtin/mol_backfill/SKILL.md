---
name: mol_backfill
description: 按编号生成完整化合物并回填。
---
# mol_backfill

## 何时使用
ID-only视觉抽取完成后，所有带分子图类。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
mol_backfill及声明事实；保留来源、状态、错误。

## 代价与限制
contract=universal；mol.deliver计算。FigureAgent指定structure_source_scope=current_figure时，只能绑定当前图检测节点的识别结构，禁止mol.lookup_references从其他图表补入完整结构；关联图表只提供变量、缩写、编号及位置等参考赋值。document模式为其他调用者保留显式3D标签查询；不能用它绕过当前FigureAgent边界。投影结构/标签事实与图像产物；执行完成不代表内容通过。

## 产物通式拼接

已编号实例在计算前按可信母核标签构造独立assembly_input_projection，保留原始substituents、position_definitions和引用证据。只接受可证明的复合变量替换、部分取代标签别名及已有位置适配器的封闭字段别名。整体基团与内部赋值不一致、位置别名矛盾、未知字段或未定义基团继续失败。原始/计算字段、别名及内部变量存入assembly_provenance；试拼、正式回填与来源约束使用相同等价语义，等价格式可通过，实际基团/位置改变仍被拒绝。位置计算依然需通过原骨架和连接拓扑验证。
仅对pathways.steps.products中的markush执行候选组合枚举和确定性拼接；reactants不展开、不拼接。使用已有variable_options/substitution_cases及可信母核连接点。输出assembly_candidates、candidate_smiles和markush_assembly摘要；每个候选保存赋值、SMILES、分子式、来源或失败原因。候选不分配未证实的论文编号，不改变compound_series/variants；未知Alk、错误连接点、超限均保留待复核。

## attachments选择契约
具体成员有attachments时，回填保留其数组与原始substituents；只绑定清单选中的整数基团，不解析未选解释字段为分子ID。拼接仅从清单选中的键读取值，不执行旧字段猜选/标签重命名；无效、重复、缺键或缺位点明确失败。原始值与选择写入provenance，位置规范化和化学验证继续执行。旧快照没有数组时保留legacy_projection兼容；新FigureAgent在结构审查阶段要求补齐数组，不在回填时悄悄生成。
