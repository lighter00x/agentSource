Only reference visually confirmed chemical boxes via 0-based mol_id. Number labels must be directly below or adjacent to the structure. Ignore detector false positives. Simple H/Me/Cl remain text. Do not emit SMILES. Preserve original labels, symbols, units, footnote markers, and uncertainties. Do not borrow labels absent from the image/caption from body paragraphs. Describe only observed domain entities in additional fields, include evidence_text and status.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

For heterogeneous multi_panel figures, also return panels as normalized bounding boxes x0,y0,x1,y1 with visual evidence. Do not split same-type curve series simply because A/B labels are printed.

Top-level status must be exactly "ok" or "unclear". Panel bounding boxes must be normalized {x0,y0,x1,y1}.

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.


Domain workflow (mandatory):
多面板先按panel_split支持的归一化框分割，子FigureAgent使用自身节点、几何与mol_id空间；父图的分段不是子面板编号空间。避免在父子之间移用编号。
能明确分类的内容优先使用专用图类Skill；未知示意图逐实体记录分子、非分子角色与evidence_text，完整/模板实例仍遵守共同契约。不能因为通用Schema允许extra就捏造新的化学字段语义；新增字段含义必须在Skill中定义后使用。
只有真实化学图才引用框。流程箭头、蛋白、细胞、球、曲线和图例不作分子；不能识别的分子保留unclear。
