# 分子图表 Skill 的复用契约

各包的 `SKILL.md` 提供实际披露的五个 L1 小节；详细步骤保存在各包自己的 `references/molecular-workflow.md`，无需让调用者访问工程外部设计文档。领域视觉包的关键约束也进入实际模型提示词。L1 不是完整提示词，L3 资源需按需读取；只加载视觉 Skill 不会自动完成全部下游化学工作。

## 读取和执行

1. 用 `find_capabilities` 发现本类 Skill，`describe_capability` 读取 L1；`read_skill_resource(name="scheme_extract", section="molecular-workflow.md#本类步骤")` 读取细则。其余索引为 `#字段与实例`、`#执行与交付`、`#失败与复核`、`#验收清单`。九个视觉包还提供 `#合法示例`，示例经当前公开产物 Schema 校验；表格示例是运行时扩展后的 TableGrid，不是视觉传输协议。
2. `load_capability` 后按当前接口执行，遵守 manifest 输入/输出 Schema、预算、Tool 白名单和用户归属检查。读取资源不授予其他 Tool 权限。FigureAgent 已负责检测→识别→编号图→专用视觉结构化→确定性回填→SAR/路线后处理；独立调用者必须自行串联这些 Skill，不能把一次视觉返回当成交付完成。
3. 中间视觉步骤只使用稳定 `mol_id` 和图上 `reference`。运行时先给编号图/切片，再给对应原图；编号可不连续、跨分段共享。识别出的化学串只供确定性化学工具使用。
4. `mol_backfill(contract="universal", extraction_payload=抽取)` 加载受归属检查的识别材料，保存检测对象与完整实例；SAR 后续映射指标并逐行保存，流程图后续按真实箭头配对投影路线。不能借其他箭头链接，也不能用范围标签生成系列笛卡尔积。
5. 验收逐框、逐实例、逐数据行、逐箭头的证据与完整结构。保存 ID-only 原始抽取、最终回填、分子对象、覆盖清单和错误。需要复核的结果保留原状态，不作为通过结果。

## 图类职责与边界

| Skill | 专属规则 |
| --- | --- |
| table_extract | 全网格及脚注转写；SAR 每行显式实例与 row_index，母核/基团来源和指标在同一源行关联；分片合并后重算行号 |
| scheme_extract | 每个箭头的端点、条件、显式变体与局部配对；范围标签不自动展开；后回填才能保存完整路线端点 |
| structures_extract | 完整分子、模板和基团分开；图上编号与检测编号分开；未印标签不造实例 |
| molecular_docking_extract | 2D结构可编号；3D姿态、残基和相互作用不当作分子框；纯文字标签只能绑定当前文档唯一候选 |
| protac_architecture_extract | 完整PROTAC与组件分开；无明确受支持连接位点时，不承诺任意三片段组装 |
| metabolic_pathway_extract | 母药、代谢物、前体与箭头分别表达；不从代谢箭头推导结构，不承诺全部代谢事实投影 |
| pharmacophore_model_extract | 特征球、向量与真实化学图分开；无支持绑定路径/明确结构的3D叠合引用保留未解析 |
| chart_digitize | 坐标、标定、曲线和真实化学插图分开；当前ChartDataset无通用实例字段，不能承诺模板组装 |
| common_extract | 优先专用图类；子面板有自己的编号空间，不能移用父图编号；领域字段不能临时造语义 |

十个支撑包 `molecule_detect`、`molecule_recognize`、`som_render`、`mol_backfill`、`sar_record`、`measurement_map`、`scheme_relations`、`markush_assemble`、`panel_split`、`figure_self_check` 同样包含包内执行与失败说明。旧流水线参考文件标明历史范围，不覆盖当前 Schema 和执行规则。

## 可靠性范围

E-SMILES分类仅是保守提示，视觉字段决定完整分子/母核/基团的使用角色。连接点、变量赋值或唯一产物未确定时保留未完成，不能拼接字符串或套用测试文献特例。普通识别图和唯一中心的显式R/S标签推导分别记录来源；多中心手性与不明确位置不能靠提示词保证正确。

文档与 Schema 一致、资源真实可读取和回归通过证明执行契约可复用。它们不代替新文献的检测召回、视觉对应、位点与手性独立内容核验，不能宣称所有图类已达到化学准确率要求。

自动检查见 `tests/unit/skills/test_molecular_workflow_disclosure.py`：真实能力卡与资源工具披露、token与截断边界、九类合法示例 Schema/语义校验、实际模型提示片段与图像顺序约束。

验证记录（2026-10-04）：35项新增专项检查、完整单元/集成760项通过，Ruff/格式检查和mypy通过；JUnit位于 samples/outputs/phase-5/20261002/tests-skill-workflows-20261004.xml。
