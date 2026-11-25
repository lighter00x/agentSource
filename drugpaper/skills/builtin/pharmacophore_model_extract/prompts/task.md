Only reference visually confirmed chemical boxes via 0-based mol_id. Number labels must be directly below or adjacent to the structure. Ignore detector false positives. Simple H/Me/Cl remain text. Do not emit SMILES. Preserve original labels, symbols, units, footnote markers, and uncertainties. Do not borrow labels absent from the image/caption from body paragraphs. Describe only observed domain entities in additional fields, include evidence_text and status.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Top-level status must be exactly "ok" or "unclear". Panel bounding boxes must be normalized {x0,y0,x1,y1}.

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.


Domain workflow (mandatory):
区分化学分子、3D叠合、药效团球/向量和距离；药效团特征不引用mol_id、不参与分子拼接。2D化学图在molecules或领域reference_molecules中用mol_id；完整实例可用compound_instances。
图例决定供体/受体/疏水等类型，不能由颜色习惯推断。3D叠合中的参考标签保留原文；当前文档只有唯一有效结构才可由后处理绑定，未支持的引用路径保留未解析，不把图形还原为原子连接。
