# 🤖 LLM 任务：代谢途径图信息提取 (v1.1)

## 🌟 任务背景

你现在是一名**药物代谢与药代动力学（DMPK）专家**。你的任务是分析一张**代谢途径图（Metabolic Pathway Plot）**及其**图注（Caption）**，提取母体药物（Parent drug）经生物转化后产生的各代谢物及其转化关系。这类信息对于理解药物体内命运、代谢稳定性评估及活性代谢物鉴定至关重要。

## 📥 输入信息

1. **目标图像**：展示母体药物到各代谢物的转化路径图，箭头旁通常标注有代谢酶。
2. **分子 index 列表**：包含图像中检测到的分子（母体药物、各代谢物）的 index 编号（由任务上下文提供）。
3. **图注/上下文**：图像标题及周围正文，用于确定药物名称、实验体系（体内/体外）及代谢酶信息。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

1. **mol_id**：分子结构检测框（bbox）**右上角的蓝色数字 index**（整数），仅当图中存在该分子的结构式时填写。
2. **reference**：该分子在**论文/图注中的文字指代符**（字符串），如 "Compound 1", "Midazolam", "M1", "M2"。
3. **判定优先级**：
   * 结构式存在 → 记录 `mol_id`；
   * 无结构式、有文字指代符 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null`。
4. **严禁输出 SMILES**：回答中不得包含任何 SMILES / 结构式 / 分子式字符串，分子结构一律用 mol_id 指代。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在母体药物或代谢物。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到连通的化学结构表达，例如明确的化学键骨架、原子标注或环结构。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* 代谢酶名称、代谢反应文字、箭头、百分比、时间点、组织标签和图例均不是分子结构，不能引用其 index。
* 若 bbox 明显不是分子或无法确认，**不得在输出的任何位置出现该 index**，也不得仅因候选 index 存在而创建代谢物节点。若母体药物或代谢物的文字编号仍有效，只保留 `reference`，将 `mol_id` 置为 `null` 或按 schema 省略。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。`parent_drug`、每个 `metabolites[]` 节点及其 `precursor` 引用都必须独立校验。

---

## 📝 核心推理指令 (Reasoning Steps)

### 步骤 1：母体药物识别 (Parent Drug Identification)

1. **定位母体分子**：通常位于图像中心或顶部，是所有箭头的起源节点。
2. **提取药物信息**：从图注中获取药物名称、编号，写入 `parent_drug.reference`；若图中存在母体结构式，同时记录 `parent_drug.mol_id`。
3. **实验体系**：记录体内/体外实验体系（如 Human liver microsomes, Rat in vivo, Recombinant CYP3A4）。

### 步骤 2：代谢物网络解析 (Metabolite Network Mapping)

对图中每一条转化箭头，提取：

1. **前体 (Precursor)**：转化的起始分子（可以是母体或另一个代谢物），记录其 `precursor.reference`（编号）与 `precursor.mol_id`（若有结构式）。
2. **代谢物 (Metabolite)**：
    * **代谢物编号**：如 M1, M2, M3 或特定名称，写入 `reference`。
    * **代谢类型**：根据结构变化判断转化类型：
        * **氧化 (Oxidation)**：羟基化、N-脱烷基、O-脱烷基、脱氢、环氧化。
        * **还原 (Reduction)**：羰基还原、硝基还原。
        * **水解 (Hydrolysis)**：酯水解、酰胺水解。
        * **结合反应 (Conjugation)**：葡萄糖醛酸化 (Glucuronidation)、硫酸化 (Sulfation)、谷胱甘肽结合 (GSH conjugation)、甲基化 (Methylation)、乙酰化 (Acetylation)。
    * **引用代谢物**：用其 `mol_id`（有结构式时）与 `reference`（有编号时）引用。
3. **代谢酶 (Enzyme)**：箭头旁标注的酶信息，如：
    * CYP 亚型：CYP3A4, CYP2D6, CYP2C9 等。
    * 其他酶：UGT, SULT, FMO, MAO, AO, NAT, GST 等。
    * 若标注为 "CYP450" 或 "Liver microsomes" 等笼统描述，如实记录。

### 步骤 3：拓扑层级解析 (Pathway Topology)

* **一级代谢物 (Phase I)**：直接从母体药物产生的代谢物。
* **二级代谢物 (Phase II)**：由一级代谢物进一步转化（如 Phase I 产物的葡萄糖醛酸结合物）。
* **序贯代谢 (Sequential Metabolism)**：如 M1 → M3（一个代谢物进一步代谢为另一个），需记录完整链条。

### 步骤 4：定量信息提取 (Quantitative Data)

* 若图中或图注中标注有**代谢物相对丰度**（如百分比、主要/次要标注）、**半衰期**或**血浆浓度**，请一并提取。

---

## ⚠️ 识别操作限制

1. **仅提取图中明确标注的信息**：若箭头旁无酶标注，`enzyme` 填 `null`。
2. **代谢位点标注**：图中常用红色或加粗标记代谢位点的结构变化，请注意识别并记录变化位点描述。
3. **多通路分支**：若母体药物同时经多条代谢途径转化，每条路径需独立记录。
4. **严禁幻觉**：图像模糊无法确认时填 `"Unclear"` / `null`。
5. **仅输出 JSON**：不得包含任何解释文字。

## 💻 输出格式 (Structured JSON)

```json
{
  "figure_context": "图注标题或描述",
  "parent_drug": {
    "reference": "母体药物名称或编号 (如 Compound 1, Midazolam)，无则 null",
    "mol_id": 0,
    "experimental_system": "实验体系 (如 Human liver microsomes, Rat in vivo, Recombinant CYP3A4)"
  },
  "metabolites": [
    {
      "reference": "代谢物编号 (如 M1, M2)，无则 null",
      "mol_id": 1,
      "metabolism_type": "代谢类型 (如 N-demethylation, Hydroxylation, Glucuronidation)",
      "phase": "代谢阶段 (Phase I / Phase II)",
      "precursor": {
        "reference": "前体分子编号 (如 Compound 1, M1)，无则 null",
        "mol_id": 0
      },
      "enzyme": "催化该转化的代谢酶 (如 CYP3A4, UGT1A1, 若未标注填 null)",
      "relative_abundance": "相对丰度 (如 Major, Minor, 35%, 若未标注填 null)",
      "site_of_metabolism": "代谢发生的位置描述 (如 N-methyl group, Phenyl ring C-4, 若可从图中推断)"
    }
  ],
  "pathway_note": "关于代谢途径的额外说明 (如主要代谢通路、种属差异等)"
}
```

> **取值规则**：`parent_drug.mol_id` / `metabolites[].mol_id` / `precursor.mol_id` = 图像中对应分子结构检测框右上角的**蓝色数字 index**（无结构式则填 `null`）；`reference` = 论文/图注中的文字编号，有结构时与 `mol_id` 同时记录。
