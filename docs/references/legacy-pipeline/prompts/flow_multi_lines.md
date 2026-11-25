# 🤖 医药化学多线路合成路径提取专家

## 🎯 任务目标

分析提供的**多线路合成流程图**、**上下文图像**及**分子 index 数据**，重构图中所有平行系列的完整反应拓扑：包括分支点定位（含不确定情况）、反应条件解密，以及任何出现的母核+取代基（SAR 模板）展开。

## 📥 输入数据清单

1. **合成路线图**：包含多个系列（如 (1), (2), (3)）、反应箭头及条件标注的图像。
2. **分子 index 列表**：包含 YOLO 检测到的候选化学结构 bbox index（由任务上下文提供）；bbox 可能是完整分子、SAR 公共骨架或取代基片段。
3. **上下文页 (Context)**：图注、正文及前后页，用于解密条件代号和推断缺失信息。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

针对图像中出现的每一个分子（前驱、产物、取代基），按以下统一规则标识：

1. **mol_id**：当前字段所对应化学结构 bbox **右上角的蓝色数字 index**（整数）；它只与图中空间位置绑定，和论文文字编号无关，也不表示 bbox 一定是完整分子。bbox 可以是完整分子、SAR 公共骨架或取代基片段。
2. **reference**：该分子在**论文/图注中的文字指代符**（字符串），如 `"1"`, `"3a"`, `"Compound 5"`, `"Intermediate IV"`；仅当存在指代符时填写。
3. **判定优先级**：
   * 结构式存在 → 必须记录 `mol_id`；
   * 无结构式、有文字指代符 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null`。
4. **小分子试剂**：未检测到结构也未编号的小分子试剂（如 "allyl bromide"）将 `mol_id` 填 `null`，英文名称字符串写入 `reference`。
5. **严禁输出 SMILES**：任何字段不得包含 SMILES / 结构式 / 分子式字符串。

> **mol_id 与 reference 的根本区别**：`mol_id` 是图像处理流水线分配的空间索引，唯一标识检测框，与论文文字无关；`reference` 是作者在论文中赋予该化合物的文字编号，与检测框无关。同一个分子的 `mol_id` 和 `reference` 可能看起来都是数字（如 `mol_id: 3` 和 `reference: "3"`），但它们来源不同，含义不同，不能互换。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在分子。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到连通的化学结构表达，例如明确的化学键骨架、原子标注或环结构。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* 文字、数字、表格单元格、坐标轴/刻度、图例、统计符号、反应条件、箭头、蛋白/残基标注、曲线及装饰线均不是分子结构；例如 `Cmax`、`Cmax/dose` 即使被框选也不得引用其 index。
* 若 bbox 明显不是分子或无法确认，**不得在输出的任何位置出现该 index**（包括 `mol_id`、`mol_ids`、整数取代基及拓扑关系），也不得仅因候选 index 存在而创建路线节点。若论文文字指代符仍有效，只保留 `reference`，将 `mol_id` 置为 `null` 或按 schema 省略。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。对本任务而言，该规则适用于每条路线的 `starting_materials`、中间体、产物、`branch_topology` 和 SAR 模板取代基。

---

## 📝 核心推理指令

### 1. 识别独立系列 (Route Identification)

* **扫描系列标签**：识别图中加粗的系列编号（如 (1), (2), (3)）或路径标签；只有出现**系列级标识**或**明显独立分块**时才建立多个 `routes`。
* **定义起点**：每个系列的 `starting_materials` 使用对象列表，每项均含 `mol_id` 与 `reference`。结构式存在时记录临时 `mol_id`；论文编号或小分子试剂名称写入 `reference`。

### 2. 以有向图建模反应拓扑 (DAG Modeling)

每个转化步骤记录为一个 **transformation** 对象，前驱用 `from` 对象列表，产物用 `to` 单对象：

* `from`：前驱列表，每项含：
  * `mol_id`：YOLO 检测框的 index 编号（整数），与图中分子的空间位置绑定，和论文文字编号无关。未检测到结构时填 `null`。
  * `reference`：论文原文中作者给该分子的编号（字符串）；未检测到结构的小分子试剂（如 "allyl bromide"）也在此处填写英文名称。
* `to`：产物对象，含：
  * `mol_id`：同上，产物的检测框 index（整数）。
  * `reference`：同上，产物在原文中的编号；无标注填 `null`。

* 由于使用显式的 from→to 对，分支结构天然在图中自描述——同一个 `from.reference` 出现在多个 transformation 中，即为分叉。每个分子节点同时保留 `reference` 与临时 `mol_id`：前者维持论文语义和拓扑关系，后者供 Molecule E-SMILES 回填。

### 3. 显式记录分支点及不确定性 (Branch Topology)

每个系列额外输出 `branch_topology` 列表，汇总该系列内所有分叉情况：

* `branch_from`：已确认的分叉出发分子对象，同时记录 `mol_id` 与 `reference`；若无法确认则填 `null`。
* `branches_to`：分叉产生的各后继产物对象列表，每项同时记录 `mol_id` 与 `reference`。
* `uncertain`：布尔值。`true` 表示无法从图像确定精确的分叉位置（如箭头起点模糊，或共享中间体标注不清）。
* `possible_branch_points`：当 `uncertain=true` 时，列举所有可能的候选分子对象；确定时为空列表。

### 4. 条件解析与解密 (Reagent & Condition Extraction)

* **代号匹配**：将箭头上的字母/数字（a, b, i, ii ...）与图注（Reagents and conditions）中的描述一一对应，填入 `actual_condition`。
* **要素完整性**：尽可能包含试剂、溶剂、温度和时间。若完全无法推断，`actual_condition` 填 `null`。

### 5. 特殊情况处理 (Status)

* 虚线箭头或图注中注明 "no yield" / "failed" 的步骤，在对应 transformation 的 `status` 字段标记为 `"failed"`；正常步骤填 `"success"`。

### 6. SAR 模板与母核+取代基展开 (SAR Expansion)

* **识别模板**：若某 transformation 的产物是包含 $R$ 基团的骨架，将该 transformation 的 `is_sar_template` 标记为 `true`。
* **模板 bbox**：当 `is_sar_template=true` 时，`to.mol_id` 记录公共骨架 bbox 的 index，不得因它不是完整分子而置空。
* **展开组合**：在 `extra` 列表中列出每个具体化合物，每项含 `reference`、`mol_id` 和 `substituents`。若变体有自己的完整结构 bbox，`extra[].mol_id` 指向完整结构；若该行只展示一个被框选的 R 片段，则 `extra[].mol_id` 指向该片段，并在对应 `substituents.R*` 中重复同一个 index；无可确认 bbox 时填 `null`。
* **取代基表示**：`substituents` 的键名与图中表头一致；值为**整数**时表示该取代基本身的 bbox index，值为**字符串**时为无 bbox 的化学简称（如 "Cl","Me"）或文字指代符。
* **禁止错误继承**：不得把模板 `to.mol_id` 自动复制给所有 `extra[]`；只有与具体变体直接对应的 bbox 才能写入 `extra[].mol_id`。
* 不含 R 基团的普通步骤：`is_sar_template: false`，`extra: []`。

---

## ⚠️ 识别操作限制

1. **以 Transformation 为单位**：每个"前驱→产物"对单独记录，不要按顺序编号替代显式的 from→to。
2. **分支点必须落回到已定义分子**：`branch_topology` 中 `branch_from` / `branches_to` / `possible_branch_points` 的每个对象，都必须能在本系列 transformations 中找到相同的 `reference` 和 `mol_id`。二者不能互相替代：`reference` 用于论文拓扑，`mol_id` 用于 Molecule 回填。
3. **系列以系列标识为准**：只有出现 `(1)(2)(3)` / `Series A/B` 等系列标识或明显独立分块时才建立多个 `routes`；单一路线内的反应条件代号 `(a)(b)(c)` 不算多系列。
4. **条件代号必须溯源**：找不到对应描述时 `actual_condition` 填 `null`。
5. **严禁幻觉**：图像模糊无法确认时，在对应字段填 `"Unclear"`；`uncertain=true` 时务必填写 `possible_branch_points`。
6. **仅输出 JSON**：不得包含任何解释文字。

---

## ⚙️ 期望的 JSON 输出格式

```json
{
  "full_scheme_title": "Scheme 2. Synthesis of three series of CRM1 inhibitors",
  "routes": [
    {
      "series_id": "(1)",
      "starting_materials": [
        {"mol_id": 0, "reference": "1"},
        {"mol_id": null, "reference": "allyl bromide"}
      ],
      "transformations": [
        {
          "from": [
            {"mol_id": 0, "reference": "1"},
            {"mol_id": null, "reference": "allyl bromide"}
          ],
          "to": {"mol_id": 1, "reference": "2"},
          "condition_code": "a",
          "actual_condition": "NBS, DBU, THF, 0 °C, 1 h",
          "is_sar_template": false,
          "extra": [],
          "status": "success"
        },
        {
          "from": [
            {"mol_id": 1, "reference": "2"}
          ],
          "to": {"mol_id": 2, "reference": "3"},
          "condition_code": "b",
          "actual_condition": "m-CPBA, DCM, 30 min",
          "is_sar_template": false,
          "extra": [],
          "status": "success"
        },
        {
          "from": [
            {"mol_id": 1, "reference": "2"}
          ],
          "to": {"mol_id": 3, "reference": "3a/3b"},
          "condition_code": "c",
          "actual_condition": "NaH, MeI, DMF, rt",
          "is_sar_template": true,
          "extra": [
            {"reference": "3a", "mol_id": null, "substituents": {"R1": "Cl", "R2": "Me"}},
            {"reference": "3b", "mol_id": null, "substituents": {"R1": "F",  "R2": "Et"}}
          ],
          "status": "success"
        }
      ],
      "branch_topology": [
        {
          "branch_from": {"mol_id": 1, "reference": "2"},
          "branches_to": [
            {"mol_id": 2, "reference": "3"},
            {"mol_id": 3, "reference": "3a/3b"}
          ],
          "uncertain": false,
          "possible_branch_points": []
        }
      ]
    },
    {
      "series_id": "(2)",
      "starting_materials": [
        {"mol_id": 0, "reference": "1"},
        {"mol_id": 4, "reference": "4"}
      ],
      "transformations": [
        {
          "from": [
            {"mol_id": 0, "reference": "1"},
            {"mol_id": 4, "reference": "4"}
          ],
          "to": {"mol_id": 5, "reference": "5"},
          "condition_code": "d",
          "actual_condition": "EDCI, HOBT, DMF, rt, 12 h",
          "is_sar_template": false,
          "extra": [],
          "status": "success"
        },
        {
          "from": [
            {"mol_id": 5, "reference": "5"}
          ],
          "to": {"mol_id": 6, "reference": "6"},
          "condition_code": "e",
          "actual_condition": null,
          "is_sar_template": false,
          "extra": [],
          "status": "failed"
        },
        {
          "from": [
            {"mol_id": 6, "reference": "6"}
          ],
          "to": {"mol_id": 7, "reference": "7"},
          "condition_code": null,
          "actual_condition": null,
          "is_sar_template": false,
          "extra": [],
          "status": "success"
        }
      ],
      "branch_topology": [
        {
          "branch_from": null,
          "branches_to": [
            {"mol_id": 7, "reference": "7"}
          ],
          "uncertain": true,
          "possible_branch_points": [
            {"mol_id": 5, "reference": "5"},
            {"mol_id": 6, "reference": "6"}
          ]
        }
      ]
    }
  ]
}
```
