Only reference visually confirmed chemical boxes via 0-based mol_id. Number labels must be directly below or adjacent to the structure. Ignore detector false positives. Simple H/Me/Cl remain text. Do not emit SMILES. Preserve original labels, symbols, units, footnote markers, and uncertainties. Do not borrow labels absent from the image/caption from body paragraphs. Describe only observed domain entities in additional fields, include evidence_text and status.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Top-level status must be exactly "ok" or "unclear". Panel bounding boxes must be normalized {x0,y0,x1,y1}.

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.


Domain workflow (mandatory):
分别标明母药、代谢物、前体和箭头方向，在各实体中引用mol_id与可见reference；完整分子用compound_instances。文字代谢物名无结构框时只能保留reference与不确定性。
代谢箭头不是识别出的分子，也不能据箭头“氧化/去甲基”自动生成SMILES。共用母核系列须有各成员明确赋值才组装。箭头、酶/组织和条件属于领域字段，当前此Skill交付领域抽取和分子回填，不承诺自动投影所有代谢反应事实。
