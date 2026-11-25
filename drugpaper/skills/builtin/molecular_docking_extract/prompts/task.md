Only reference visually confirmed chemical boxes via 0-based mol_id. Number labels must be directly below or adjacent to the structure. Ignore detector false positives. Simple H/Me/Cl remain text. Do not emit SMILES. Preserve original labels, symbols, units, footnote markers, and uncertainties. Do not borrow labels absent from the image/caption from body paragraphs. Describe only observed domain entities in additional fields, include evidence_text and status.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Top-level status must be exactly "ok" or "unclear". Panel bounding boxes must be normalized {x0,y0,x1,y1}.

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.

For 3D docking stick representations, preserve explicitly printed/caption ligand labels in molecules (e.g. (S)-26 and (R)-26), with mol_id=null when there is no 2D numbered box. Code resolves these explicit labels against document structures after extraction. Never invent SMILES or a numbered box from a 3D pose. molecule_inventory entries contain ONLY mol_id, disposition, evidence_text; put reference labels in molecules, not inventory.

For 3D sticks set representation="3d_stick_models". Return residues as a top-level list of {name,panel_id,evidence_text} from visibly printed residue labels. Preserve panel descriptions and ligand identities. State interaction types only when supported by the drawing/legend; do not invent distances.


Domain workflow (mandatory):
2D配体画有编号框时使用mol_id，完整实例可以compound_instances引用；蛋白、残基和3D棒状姿态不是2D识别框。
纯3D图设置representation="3d_stick_models"，molecules中保留印刷配体reference，mol_id=null；mol_backfill用mol.lookup_references仅在当前文档绑定唯一有效完整结构。不能借邻图标签或外部数据库猜结构。
逐面板记录残基residues与evidence_text，只记录可见/图例支持的距离和相互作用。R/S按明确标签推导时保留来源，不能声称从姿态识别出手性。文档绑定缺失/冲突时needs_review。
