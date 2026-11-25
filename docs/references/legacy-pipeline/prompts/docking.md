针对 **[Molecular_Docking] 分子对接图**，提取的核心目标是识别**受体（蛋白质）- 配体（小分子）**之间的相互作用网络。此类图像通常包含复杂的 3D 空间关系和文本标注。
# 🤖 LLM 任务：分子对接图像信息深度提取 (v1.1)

## 🌟 任务背景

你现在是一名**计算药物化学专家**。你的任务是分析一张**分子对接图（Molecular Docking Plot）**及其**图注（Caption）**，提取其中的关键结合信息。这些信息对于理解药物分子如何与靶点蛋白结合至关重要。

## 📥 输入信息

1. **目标图像**：分子对接结果的 2D 或 3D 可视化图像，展示配体与蛋白结合口袋的空间关系。
2. **图注/上下文**：图像标题及周围正文，用于确定靶点蛋白、PDB ID、配体名称及对接软件信息。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

1. **mol_id**：配体分子结构检测框（bbox）**右上角的蓝色数字 index**（整数），仅当图中存在该配体的结构式时填写。
2. **reference**：配体在**论文/图注中的文字指代符**（字符串），如 "Compound 12", "Inhibitor A"。
3. **判定优先级**：
   * 结构式存在 → 记录 `mol_id`；
   * 无结构式、有文字指代符 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null`。
4. **严禁输出 SMILES**：回答中不得包含任何 SMILES / 结构式 / 分子式字符串。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在配体分子。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到可识别的配体化学结构表达。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* 蛋白质 cartoon/surface、氨基酸残基标签、虚线相互作用、距离数值、坐标轴、图例和文字说明均不是可回填的分子结构；例如残基名或测量数字被框选时不得引用其 index。
* 若 bbox 明显不是配体结构或无法确认，**不得在输出的任何位置出现该 index**。若图注仍给出配体名称，只保留 `docking_metadata.ligand.reference`，将其 `mol_id` 置为 `null` 或按 schema 省略。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。

---

## 🔍 提取重点 (Extraction Focus)

### 1. 实体识别 (Entity Identification)
*   **靶点蛋白 (Target Protein)**：提取蛋白名称、缩写及 PDB ID（如：3VRI, EGFR, Kinase domain）。
*   **配体分子 (Ligand)**：识别图中配体（通常为彩色棒状模型），按统一规则记录其 `reference`（编号/通用名，如 Compound 12b, Inhibitor A）与 `mol_id`（若有检测框蓝色 index）。

### 2. 相互作用网络 (Interaction Network) - **核心任务**
识别图中表示相互作用的**虚线（Dashed lines）**，并提取：
*   **氨基酸残基 (Residues)**：名称+编号（例：Asp189, Phe216, Glu102）。
*   **作用类型 (Interaction Type)**：
    *   **Hydrogen Bond (H-bond)**：通常表现为指向极性原子的虚线。
    *   **$\pi$-$\pi$ Stacking**：表现为两个环状结构之间的平行或垂直虚线。
    *   **Cation-$\pi$**：电荷中心与芳环之间的连线。
    *   **Hydrophobic Interaction**：位于疏水口袋中的接触。
    *   **Halogen Bond**：卤素原子（F, Cl, Br, I）相关的特殊相互作用。
*   **键距 (Distance)**：如果图中显式标注了数值（如 2.8 $\mathring{A}$），请务必提取。

### 3. 结合口袋描述 (Binding Pocket Context)
*   **口袋特征**：是否提到是变构位点（Allosteric site）或活性中心（Catalytic site）。
*   **表面展示**：图像是否展示了电荷表面（Electrostatic surface）或疏水表面（Hydrophobic surface）。

---

## 🛠 提取逻辑 (Execution Logic)

1.  **第一步：文本对照**。阅读图注（Caption），确定 PDB ID 和配体名称（`reference`）。
2.  **第二步：视觉扫描**。定位图像中的配体，识别其周围标注的氨基酸残基标签，并记录配体的 `mol_id`（若有蓝色 index）。
3.  **第三步：连线匹配**。观察连接配体与残基的虚线：
    *   若虚线连向残基的**主链/侧链原子**，判定为 H-bond 或盐桥。
    *   若虚线连向残基的**芳香环中心**，判定为 $\pi$ 相关作用。
4.  **第四步：数据对齐**。将视觉发现与图注内容整合，剔除背景噪音（如水分子交互，除非显式强调）。

---

## ⚠️ 识别操作限制

1. **残基必须来自图像标签**：仅提取图中明确标注的氨基酸；无法确认编号时填 `Unknown`。
2. **区分侧链与骨架**：尽量区分相互作用发生在残基侧链（Side chain）还是骨架（Backbone）上。
3. **多视图合并**：若有多视图（Panel A: 2D, Panel B: 3D），优先合并两者信息，2D 图通常对作用类型标注更清晰。
4. **颜色映射优先于惯例**：若图注定义颜色含义（如"绿色虚线=H-bond，紫色=π 作用"），按图注执行。
5. **严禁幻觉**：图像模糊无法确认时填 `"Unclear"` / `null`。
6. **仅输出 JSON**：不得包含任何解释文字。

## 💻 输出格式 (Structured JSON)

```json
{
  "docking_metadata": {
    "target_protein": "靶点蛋白全称或缩写 (例如: EGFR, Mpro)",
    "pdb_id": "蛋白质结构在PDB数据库中的4位唯一标识符 (例如: 6LU7)",
    "ligand": {
      "reference": "配体在论文中的指代符 (例如: Compound 12, Imatinib)，无则 null",
      "mol_id": 0
    },
    "software_used": "生成该对接图的模拟软件名称 (若图中提及，如: Glide, AutoDock)，无则 null"
  },
  "interaction_details": [
    {
      "residue": "参与结合的氨基酸残基名称+编号 (例如: Asp189, Phe216)",
      "interaction_type": "相互作用力物理类型 (例如: Hydrogen Bond, Pi-Pi Stacking, Salt Bridge)",
      "atom_involved": "残基中具体参与作用的原子或基团 (例如: Side chain oxygen, Amide NH)",
      "distance_angstrom": "键距数值 (单位为埃 Å，若图中未标注则填 null)",
      "is_backbone": "布尔值，表示作用是否发生在蛋白主链上 (true为是，false为侧链)"
    }
  ],
  "pocket_characteristics": {
    "surface_type": "结合口袋表面的渲染方式 (例如: Hydrophobic surface, Electrostatic map)，无则 null",
    "key_functional_regions": ["该结合区域的生物学功能定义 (例如: ATP-binding site, Hinge region)"]
  },
  "summary_statement": "用一句话概括分子结合的核心模式，描述关键作用力和构效关系贡献。",
  "extraction_confidence": {
    "score": 0.95,
    "reasoning": "说明评分理由，例如：'残基标签清晰，但键距标注略显模糊'。"
  }
}
```

> **取值规则**：`ligand.mol_id` = 图像中配体分子结构检测框右上角的**蓝色数字 index**（图中无配体结构式则填 `null`）；`ligand.reference` = 配体在论文中的文字指代符，二者有结构时同时记录。
