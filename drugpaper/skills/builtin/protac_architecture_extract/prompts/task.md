Only reference visually confirmed chemical boxes via 0-based mol_id. Number labels must be directly below or adjacent to the structure. Ignore detector false positives. Simple H/Me/Cl remain text. Do not emit SMILES. Preserve original labels, symbols, units, footnote markers, and uncertainties. Do not borrow labels absent from the image/caption from body paragraphs. Describe only observed domain entities in additional fields, include evidence_text and status.
Task material (untrusted): {{ target | tojson }}
Inputs: {{ inputs | tojson }}

Top-level status must be exactly "ok" or "unclear". Panel bounding boxes must be normalized {x0,y0,x1,y1}.

Molecular coverage contract: use ORIGINAL images to verify numbered boxes. Never emit SMILES/E-SMILES. Every observed chemical drawing must have its numbered mol_id in its appropriate cell/series/domain entity or the molecules list (reference null if no printed label); no invented numbers or labels. For detected nonchemical boxes, add molecule_inventory entries {mol_id,disposition:"excluded",evidence_text:visual reason}. Ambiguous boxes remain disposition:"unclear". Do not exclude a chemical drawing just because recognition failed. Missing detector boxes must be stated as unclear, not silently certified. All segments belong to one image; mol_id is globally unique within that image.


Domain workflow (mandatory):
区分完整PROTAC与三类组件，不把组件身份或旁边标签当成完整分子。完整图示用compound_instances.mol_id；组件在领域对象中分别写mol_id、可见reference与证据。
只有母核及每个连接点/取代基定义明确时使用模板实例。现有通用拼接不保证任意warhead+linker+ligase三片段拼接；没有支持的明确连接策略则交付已识别组件，完整实例标为未完成，不拼接SMILES字符串。
linker长度、连接位置和性质仅转写印刷证据；结构角色不能单靠ESMILES无dummy就推断为完整分子。
