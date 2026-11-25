根据附图和图注分类。类别顺序对应编号 1..13：synthetic_scheme_single, synthetic_scheme_multi, sar_table, reported_structures, molecular_docking, protac_architecture, metabolic_pathway, pharmacophore_model, property_plot, other, data_table, data_chart, multi_panel。
单一路线的条件 (a)(b)(c)、i/ii 和分支箭头不等于平行系列；只有明确独立 Series 或多条独立路线才判 multi。异质子图才判 multi_panel。无结构的 ADMET/PK 表为 data_table；无结构的曲线为 data_chart。只描述图像，不生成 SMILES。证据不足返回 other，needs_review=true。
图注及材料：{{ target | tojson }}
代码先验：{{ inputs | tojson }}
判别特征（先看真实图形，再结合图注，不以标题中的靶点名称代替图形）：
- reported_structures：一个或多个二维化学结构，带编号或药名；没有蛋白三维背景、残基标签或相互作用线时，不得判 molecular_docking。
- molecular_docking：必须看到蛋白 surface/cartoon、结合口袋、残基标签或蛋白-配体相互作用；纯二维化学结构不是对接图。
- sar_table：表格同时包含母核结构和 R/X 等取代基列或结构与活性对应关系；不得因主体是数字就忽略表上方的母核。
- synthetic_scheme_single：单一起点到终点的反应/优化流向；反应条件字母、分支条件及共用母核不单独构成平行系列。
- synthetic_scheme_multi：独立起始原料对应不同反应系列，或上下明确分隔的独立路线；必须说明不同系列的依据。
- protac_architecture：Warhead、Linker、E3 ligand 三类模块连接关系。
- metabolic_pathway：代谢酶 CYP/UGT、microsomes 与 M1/M2 等代谢产物。
- pharmacophore_model：HBA/HBD/HYD 等特征球、距离与角度。
- property_plot：带坐标轴的数据点旁同时显示化学结构；纯曲线不能判 property_plot。
- data_table：无化学结构的表格，包括 ADMET/PK/选择性数据；R、Me 等文字本身不是结构式。
- data_chart：无化学结构的折线、柱状、散点或剂量反应图；多个同类曲线 panel 仍为 data_chart。
- multi_panel：不同视觉类型共同组成的子图，单纯 A/B/C 标号不充分。
- other：以上条件均不满足，或无法可靠辨认；低置信度标 needs_review。
