# 🤖 LLM 任务：药效团模型图信息提取 (v1.1)

## 🌟 任务背景

你现在是一名**计算药物化学与分子模拟专家**。你的任务是分析一张**药效团模型图（Pharmacophore Model Plot）**及其**图注（Caption）**，提取药效团特征元素（Pharmacophoric Features）、空间约束及其与分子结构的对应关系。药效团模型是药物设计的核心工具，理解其空间分布对于先导化合物优化与虚拟筛选至关重要。

## 📥 输入信息

1. **目标图像**：展示药效团模型的图像，通常为分子骨架上覆盖有彩色球体/区域，并标注有距离和角度。
2. **分子 index 列表**：包含图像中检测到的参考分子的 index 编号（由任务上下文提供）；若图中无参考分子，该列表为空。
3. **图注/上下文**：图像标题及正文，用于确定药效团模型的生成方法、训练集和靶点信息。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

1. **mol_id**：参考/叠合分子结构检测框（bbox）**右上角的蓝色数字 index**（整数），仅当图中存在该分子的结构式时填写。
2. **reference**：该分子在**论文/图注中的文字指代符**（字符串）。
3. **判定优先级**：
   * 结构式存在 → 记录 `mol_id`；
   * 无结构式、有文字指代符 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null` / `[]`。
4. **无参考分子处理**：药效团图可能只展示特征球体、不包含参考分子结构，此时 `reference` / `mol_id` 相关字段填 `null` / `[]`。
5. **严禁输出 SMILES**：回答中不得包含任何 SMILES / 结构式 / 分子式字符串。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在参考/叠合分子。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到可识别的分子化学结构表达。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* 药效团彩色球体、excluded volume、距离/角度标注、方向箭头、特征标签和网格均不是分子结构，不能引用其 index。
* 若 bbox 明显不是分子或无法确认，**不得在输出的任何位置出现该 index**。若参考分子名称仍有效，只保留 `reference`，将 `mol_id` 置为 `null` 或按 schema 省略；不要为误检创建叠合分子条目。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。`model_metadata.reference_molecules` 与 `overlay_info` 中的 index 必须分别校验。

---

## 📝 核心推理指令 (Reasoning Steps)

### 步骤 1：模型元数据提取 (Model Metadata)

1. **靶点信息**：从图注中提取该药效团模型所针对的**生物靶点**（如 EGFR kinase, 5-HT2A receptor）。
2. **生成方法**：识别药效团是基于**配体（Ligand-based）**还是**结构（Structure-based）**生成的。
3. **使用软件**：若提及软件名称（如 Phase, LigandScout, MOE, Catalyst/HypoGen），请记录。
4. **参考分子（可选）**：若图中显示了叠加在药效团上的参考分子，逐一记录其 `reference`（编号/名称）与 `mol_id`（蓝色 index）；若图中无参考分子，`reference_molecules` 填空数组 `[]`。

### 步骤 2：药效团特征提取 (Feature Extraction)

识别图中每个**彩色球体或标注区域**，提取以下信息：

1. **特征类型 (Feature Type)**：
    * **HBA (Hydrogen Bond Acceptor)**：氢键受体，通常为含孤对电子的原子（O, N, S）。
    * **HBD (Hydrogen Bond Donor)**：氢键供体，通常为 N-H 或 O-H。
    * **HYD / Hydrophobic**：疏水基团，通常为烷基或芳环。
    * **AR / Aromatic Ring**：芳香环特征。
    * **PI / Positive Ionizable**：正电荷中心（如质子化胺）。
    * **NI / Negative Ionizable**：负电荷中心（如羧酸根）。
    * **XBD (Halogen Bond Donor)**：卤键供体。
2. **颜色编码**：记录每种特征对应的颜色（如绿色=HBD，红色=HBA，蓝色=PI，黄色=HYD）。
3. **空间坐标/位置**：若图中标注了特征的空间位置或编号（如 F1, F2, F3），请记录。
4. **排除体积 (Excluded Volume)**：若图中存在灰色或黑色区域表示空间排斥区，请标注。

### 步骤 3：空间约束提取 (Spatial Constraints)

1. **特征间距离 (Inter-feature Distance)**：提取图中标注的任意两个特征之间的距离（单位通常为 $\mathring{A}$）。
2. **角度约束 (Angle Constraint)**：若有方向性箭头或角度标注（如 HBD 的供体方向），请记录。
3. **容差半径 (Tolerance Radius)**：若球体大小代表容差范围，记录其数值（若有标注）。

### 步骤 4：分子叠合信息 (Molecular Overlay)

* 若图中显示了**一个或多个分子叠合（Overlay/Alignment）**在药效团模型上，逐一记录其 `reference` 与 `mol_id`，以及匹配程度。
* 若标注有 **Fitness Score** 或 **PhaseScreenScore**，请提取。

---

## ⚠️ 识别操作限制

1. **颜色一致性**：若图注定义了颜色映射（如 "green spheres represent HBD"），严格遵循该定义；若无定义，采用领域通用惯例但在 `extraction_confidence.reasoning` 中说明。
2. **2D vs 3D**：部分图像为 2D 平面投影，注意距离数值可能是投影距离而非真实 3D 距离，如有标注请如实记录。
3. **多模型对比**：若图中展示了多个药效团假设（Hypothesis）的对比，请逐一独立提取。
4. **特征类型不可确认时**：标记为 `"Unclear"`，不得臆造。
5. **严禁幻觉**：图像模糊无法确认时填 `"Unclear"` / `null`。
6. **仅输出 JSON**：不得包含任何解释文字。

## 💻 输出格式 (Structured JSON)

```json
{
  "figure_context": "图注标题或描述",
  "model_metadata": {
    "target": "靶点蛋白或受体名称",
    "approach": "Ligand-based / Structure-based / Combined",
    "software": "使用的软件名称 (若有, 否则 null)",
    "reference_molecules": [
      {
        "reference": "参考分子编号/名称，无则 null",
        "mol_id": 0
      }
    ],
    "hypothesis_id": "药效团假设编号 (如 Hypo1, Model A, 若有)"
  },
  "pharmacophoric_features": [
    {
      "feature_id": "特征编号 (如 F1, Feature A, 或自定义序号)",
      "feature_type": "特征类型 (HBA / HBD / HYD / AR / PI / NI / XBD / Excluded Volume)",
      "color": "图中对应颜色 (如 green, red, cyan)",
      "mapped_atom_or_group": "该特征对应的分子结构位置描述 (如 Carbonyl oxygen, Phenyl ring, NH of amide, 若可从图中推断)",
      "tolerance_radius_angstrom": "容差半径 (若有标注, 否则 null)"
    }
  ],
  "spatial_constraints": [
    {
      "feature_pair": ["F1", "F2"],
      "distance_angstrom": "两特征间距离 (如 4.5)",
      "angle_degree": "方向性角度 (若有标注, 否则 null)"
    }
  ],
  "overlay_info": [
    {
      "reference": "叠合分子编号/名称，无则 null",
      "mol_id": 1,
      "fitness_score": "匹配得分 (若有, 否则 null)"
    }
  ],
  "model_statistics": {
    "number_of_features": "药效团特征总数",
    "excluded_volumes_count": "排除体积数量 (若有)",
    "additional_metrics": {}
  },
  "extraction_confidence": {
    "score": 0.90,
    "reasoning": "说明评分理由，例如：'特征球体颜色清晰，但部分距离标注因图像分辨率较低难以精确读取'。"
  }
}
```

> **取值规则**：`reference_molecules[].mol_id` / `overlay_info[].mol_id` = 图像中对应分子结构检测框右上角的**蓝色数字 index**（图中无参考分子则填 `[]` / `null`）；`reference` = 论文/图注中的文字指代符，有结构时与 `mol_id` 同时记录。
