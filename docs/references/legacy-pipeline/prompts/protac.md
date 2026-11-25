# 🤖 LLM 任务：PROTAC/双功能分子架构图信息提取 (v1.1)

## 🌟 任务背景

你现在是一名**靶向蛋白降解领域的药物化学专家**。你的任务是分析一张**PROTAC（蛋白水解靶向嵌合体）或其他双功能分子架构图**及其**图注（Caption）**，提取分子各功能模块的结构信息与设计逻辑。这类分子由"弹头-连接链-配体"三元件组成，理解其模块化设计对于药物研发至关重要。

## 📥 输入信息

1. **目标图像**：包含 PROTAC 或双功能分子架构的展示图，通常以色块/框图标注各功能区。
2. **分子 index 列表**：包含图像中检测到的分子（完整 PROTAC、弹头、E3 配体等）的 index 编号（由任务上下文提供）；若图像为纯示意图，可能没有分子 index，此时所有 `mol_id` 字段填 `null`。
3. **图注/上下文**：图像标题及周围正文，用于确定靶点、E3 连接酶类型及设计策略。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

1. **mol_id**：当前字段所对应化学结构 bbox **右上角的蓝色数字 index**（整数）。它只是定位索引，不表示 bbox 一定是完整 PROTAC；顶层 `molecules[].mol_id` 对应完整 PROTAC，`warhead.mol_id` / `e3_ligand.mol_id` 对应相应组件或片段。
2. **reference**：该分子在**论文/图注中的文字指代符**（字符串），如 "PROTAC-1", "Compound 15a", "JQ1", "Pomalidomide"。
3. **判定优先级**：
   * 结构式存在 → 记录 `mol_id`；
   * 无结构式、有文字指代符 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null`。
4. **示意图处理**：PROTAC 图多为架构示意图（色块/框图），不含可检测分子结构，此时 `mol_id` 填 `null`，用 `chemical_name` 描述模块。
5. **禁止跨层复用**：除非同一个 bbox 确实完整覆盖对应的两个层级，否则不得把组件 bbox index 写入顶层完整 PROTAC 的 `mol_id`，也不得把完整 PROTAC bbox index 写入 `warhead` 或 `e3_ligand`。
6. **严禁输出 SMILES**：回答中不得包含任何 SMILES / 结构式 / 分子式字符串。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在分子或功能片段。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到连通的化学结构表达，例如明确的化学键骨架、原子标注或环结构。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* `Warhead`、`Linker`、`E3 ligand` 等文字标签、彩色色块、连接箭头、蛋白图标及架构框均不是分子结构，不能引用其 index。
* 若 bbox 明显不是分子或无法确认，**不得在输出的任何位置出现该 index**。仍可保留模块的 `chemical_name` 或论文 `reference`，但对应 `mol_id` 必须为 `null` 或按 schema 省略。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。完整 PROTAC、warhead、linker 和 E3 ligand 的 index 必须分别通过该校验。

---

## 📝 核心推理指令 (Reasoning Steps)

### 步骤 1：模块划分与识别 (Module Segmentation)

1. **识别三元架构**：在图像中定位以下三个核心区块：
    * **靶蛋白配体 / 弹头 (Warhead / POI Ligand)**：与目标蛋白（Protein of Interest）结合的药效片段。
    * **连接链 (Linker)**：连接两个功能端的柔性或刚性链段。
    * **E3 泛素连接酶配体 (E3 Ligand / Recruiter)**：招募 E3 连接酶（如 CRBN, VHL, IAP, MDM2）的片段。
2. **色块/标签对照**：图中通常以不同颜色区域或文字标签（如 "Warhead", "Linker", "E3 Ligand"）区分各模块，请记录各模块的标注方式。

### 步骤 2：各模块信息提取 (Module Detail Extraction)

1. **弹头模块 (Warhead)**：
    * 提取弹头所靶向的**目标蛋白**名称（如 BRD4, AR, BTK）。
    * 识别弹头的化学类型或已知药物名（如 JQ1, Dasatinib, Ibrutinib 片段），写入 `chemical_name`；其在论文中的编号/代号写入 `reference`。
    * 若弹头是图像中检测到的分子，用其 `mol_id` 引用；若为示意图无检测分子，`mol_id` 填 `null`。
2. **连接链模块 (Linker)**：
    * 提取连接链的**类型**（如 PEG chain, alkyl chain, rigid piperazine linker）。
    * 若图中标注了链长或原子数（如 "n=3", "C6 linker"），请记录。
    * 识别连接方式（如 amide bond, triazole click, ether linkage）。
3. **E3 配体模块 (E3 Ligand)**：
    * 提取招募的 **E3 连接酶**名称（如 Cereblon/CRBN, VHL, cIAP1）。
    * 识别 E3 配体的已知化学名（如 Thalidomide, Pomalidomide, VH032, Bestatin），写入 `chemical_name`；其在论文中的编号/代号写入 `reference`。
    * 若 E3 配体是图像中检测到的分子，用其 `mol_id` 引用；若为示意图无检测分子，`mol_id` 填 `null`。

### 步骤 3：设计变体与 SAR 信息 (Design Variants)

* 若图像中展示了**多个 PROTAC 变体**（如不同连接链长度、不同弹头），请逐一提取并记录差异。
* 若图中包含**降解活性数据**（如 $DC_{50}$, $D_{max}$, 降解率），请一并提取。

### 步骤 4：上下文补充 (Context Integration)

* 从图注中提取**降解机制说明**（如 "induces ubiquitination and proteasomal degradation of BRD4"）。
* 记录任何提及的**三元复合体（Ternary complex）**信息。

---

## ⚠️ 识别操作限制

1. **模块边界判定**：若图中未以明确分隔线划分三个区域，根据化学结构特征（如 phthalimide 归属 CRBN 配体、quinazoline 可能是弹头）辅助判定，但需在 `extraction_confidence.reasoning` 中说明。
2. **非典型架构**：部分双功能分子可能不是经典三元结构（如分子胶 Molecular Glue、LYTAC、AUTAC），请根据实际情况调整模块命名。
3. **严禁幻觉**：仅提取图像中明确标注或图注中出现的信息；某模块标注模糊时填 `"Unclear"` / `null`。
4. **仅输出 JSON**：不得包含任何解释文字。

## 💻 输出格式 (Structured JSON)

```json
{
  "figure_context": "图注标题或描述",
  "molecule_type": "PROTAC / Molecular Glue / LYTAC / AUTAC / Bifunctional",
  "molecules": [
    {
      "reference": "分子在论文中的指代符 (如 PROTAC-1, Compound 15a)，无则 null",
      "mol_id": 0,
      "warhead": {
        "target_protein": "靶蛋白名称 (如 BRD4, EGFR)",
        "chemical_name": "弹头化学名或药物片段名 (如 JQ1-derived)，无则 null",
        "reference": "弹头在论文中的指代符 (如 JQ1)，无则 null",
        "mol_id": 1
      },
      "linker": {
        "type": "连接链类型 (如 PEG, alkyl, rigid)，无则 null",
        "length": "链长描述 (如 n=4, C8, 12 atoms)，无则 null",
        "attachment_chemistry": "连接化学键类型 (如 amide, click triazole, ether)，无则 null"
      },
      "e3_ligand": {
        "e3_ligase": "E3 连接酶名称 (如 CRBN, VHL, cIAP1)",
        "chemical_name": "E3 配体化学名 (如 Pomalidomide, VH032)，无则 null",
        "reference": "E3 配体在论文中的指代符，无则 null",
        "mol_id": 2
      },
      "activity_data": {
        "DC50": "半数降解浓度 (若有)",
        "Dmax": "最大降解率 (若有)",
        "other_metrics": {}
      }
    }
  ],
  "mechanism_note": "图注中关于降解机制的说明 (若有, 否则 null)",
  "extraction_confidence": {
    "score": 0.90,
    "reasoning": "说明评分理由，例如：'模块边界由色块明确划分，但连接链类型标注较小需推断'。"
  }
}
```

> **`mol_id` 取值规则**：
> * `mol_id` / `warhead.mol_id` / `e3_ligand.mol_id` 的值为图像中对应分子结构检测框右上角的**蓝色数字 index**。
> * 若图像为**纯架构示意图（无实际分子检测）**，上述 `mol_id` 字段填 `null`，以 `chemical_name` 描述模块。
> * `reference` 为该分子/模块在论文中的文字指代符，有结构时与 `mol_id` 同时记录。
