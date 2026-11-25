# 历史规则迁移说明

旧版将全部组合当成论文已合成实例、复制模板编号给变体以及把每个箭头视为具体反应的规则已撤销。局部取代方案应按当前markush契约展开。

当前执行契约以 manifest、schemas、prompts/task.md 和 molecular-workflow.md 为准：

- 编号范围表示化合物集合；通式的变量和取代方案在反应物/产物markush内展开，但不能据此分配具体编号结构。
- 通用母核使用 scaffold_mol_id；变量选项保存在 compound_series.variable_definitions，不是具体赋值。
- 看不到逐成员定义时保留 unresolved；不得假设已有未提供的正文、Table或Scheme信息。
- 有证据的具体实例才进入 variants；独立系列不能拼成连续路线或生成笛卡尔积。
- 逐箭头保留端点与证据，并区分 reaction、optimization、selection 和 unresolved。
- 视觉模型只输出ID及印刷文字，不生成SMILES、分子式或拼接产物。
