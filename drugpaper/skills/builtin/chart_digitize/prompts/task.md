Extract chart axes, legend RGB colors, ticks and pixel positions, plot_area normalized [0,1], series and error bars. Pixel coordinates refer to the actual supplied image dimensions. Linear/log/category types must be explicit. Include pixel points only where CV cannot separate a series; property_plot also binds reference/mol_id in the same call. Mark unreadable chart unclear.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.


Domain workflow (mandatory):
先确认坐标刻度、单位、线性/对数尺度和图例，再提取像素点，代码标定回绘并计算检查指标。series.reference是印刷曲线/化合物标签，series.mol_id只能指向实际化学绘图，不能指向曲线或图例误检。
未与某曲线明确关联的化学插图放molecules；曲线无化学结构不强造分子对象。ChartDataset没有通用compound_instances字段，不能输出母核拼接记录后被后处理丢弃；复杂模板必须说明unclear并交给能表达该领域实例的图类Skill，不承诺该曲线Skill完成模板组装。
数值性质经chart_measurements处理；分子关联经mol_backfill。坐标标定失败不抹掉已完成的化学引用，保留失败证据。
