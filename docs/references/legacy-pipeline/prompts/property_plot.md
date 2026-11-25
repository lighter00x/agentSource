# 🤖 LLM 任务：结构-性质趋势图信息提取 (v1.1)

## 🌟 任务背景

你现在是一名**药物化学数据分析专家**。你的任务是分析一张**结构-性质趋势图（Property Plot）**及其**图注（Caption）**，提取图中展示的分子结构与其对应的物理化学或生物活性数据。这类图像的核心特征是在**直角坐标系上的数据点旁悬浮着分子结构图**，用于直观呈现构效关系或性质优化趋势。

## 📥 输入信息

1. **目标图像**：包含坐标轴、数据点及分子结构标注的趋势图或散点图。
2. **分子指代数据**：图像中可能涉及到一些论文中出现的分子，一般为论文指代符（如 Compound 1）或常见分子的化学简称。
3. **图注/上下文**：图像标题及正文，用于确定坐标轴含义、性质指标定义及研究目标。

---

## ⚠️ 分子标识统一规则 (mol_id / reference)

1. **mol_id**：该数据点所对应化学结构 bbox **右上角的蓝色数字 index**（整数），仅当结构在图中被检测框包围时填写。它只是图像内的定位索引，不自行证明结构是完整分子；若图中明确只展示与该数据点对应的局部结构片段，仍记录其 index，但不得补全或臆造未展示部分。
2. **reference**：该分子在**论文/图注中的文字指代符**（字符串），如 "Compound 3", "Lead 1", "Gen-2"。
3. **判定优先级**：
   * 数据点旁有分子结构式（带蓝色 index）→ 记录 `mol_id`；
   * 无结构式、只有名称/编号/简称 → 记录 `reference`；
   * 二者都存在 → **同时记录 `mol_id` 和 `reference`**；
   * 均无 → 填 `null`。
4. **严禁输出 SMILES**：任何字段不得包含 SMILES / 结构式 / 分子式字符串。

### 候选 index 真实性校验（强制）

* 上下文提供的 index 只是 YOLO 产生的**候选 bbox**，可能是误检，不代表框内一定存在数据点对应的分子。
* 写入任何 `mol_id` 前，必须直接检查该 bbox 内是否能看到连通的化学结构表达，例如明确的化学键骨架、原子标注或环结构。蓝色 index、红色框或候选列表本身都不能作为分子存在的证据。
* 数据点、曲线、柱形、误差线、坐标轴/刻度、图例、数值、统计符号和化合物文字名称均不是分子结构；例如 `Cmax`、`Cmax/dose` 即使被框选也不得引用其 index。
* 若 bbox 明显不是分子或无法确认，**不得在输出的任何位置出现该 index**。数据点仍可按图中名称记录 `reference` 和数值，但 `mol_id` 必须为 `null` 或按 schema 省略。
* 允许候选 index 不出现在最终 JSON；不得为了覆盖所有候选框而强行分配。每个 `data_points[].mol_id` 都必须独立通过该校验。
* `reference` 负责绑定论文中的化合物编号，`mol_id` 负责绑定图中实际展示的 bbox；即使 bbox 只展示局部结构，两者也要在同一 `data_points[]` 条目内同时保留，不能用编号替代 index。

---

## 📝 核心推理指令 (Reasoning Steps)

### 步骤 1：坐标系解析 (Axes Interpretation)

1. **X 轴**：提取轴标签、单位及数据含义（如 $LogP$, $IC_{50}$ (nM), Compound number, Generation）。
2. **Y 轴**：提取轴标签、单位及数据含义（如 Metabolic stability (%), $K_i$ ($\mu$M), Selectivity Index）。
3. **辅助轴**：若存在双 Y 轴或气泡大小代表第三维度，请同时记录。
4. **图表类型**：散点图（Scatter）、柱状图（Bar）、折线图（Line）、气泡图（Bubble）、雷达图（Radar）或其组合。

### 步骤 2：数据点与分子结构关联 (Data-Structure Mapping)

对图中**每一个标注了分子结构的数据点**：

1. **定位数据点**：读取该点在 X 轴和 Y 轴上的数值（尽可能精确，可根据网格线估算）。
2. **提取编号**：识别数据点或分子旁的编号/名称标注，写入 `reference`。
3. **分子指代 (mol_id)**：若该数据点对应的分子结构在图像中被检测框包围且右上角有蓝色数字 index，用该数字作为 `mol_id`；若图中没有分子结构式，只有名称、编号或简称，则 `mol_id` 填 `null`，将指代符写入 `reference`。
4. **额外标注**：是否有箭头、高亮框或文字注释（如 "Hit", "Lead", "Optimized"）标记为关键化合物，写入 `annotation`。

### 步骤 3：趋势与区域识别 (Trend & Region Analysis)

1. **趋势线**：若图中绘有趋势线、拟合曲线或回归方程，提取方程和 $R^2$ 值。
2. **分区标记**：若图中有划分区域的虚线或色块（如 "Drug-like space", "Lipinski cutoff", 灰色禁区），记录区域边界与含义。
3. **目标窗口**：若标注有目标属性范围（如 "Target: LogP 2-4, MW < 500"），请提取。

### 步骤 4：无结构数据点处理 (Non-structural Data Points)

* 若图中有部分数据点**未标注分子结构**（仅为普通点），记录其数量和分布趋势，但无需逐一提取坐标。
* 若存在**颜色分组或形状分组**（如不同系列用不同颜色），记录图例（Legend）内容。

---

## ⚠️ 识别操作限制

1. **坐标读取诚实标注**：坐标数值从图中目测读取，若无法精确读取请给出估计范围（如 "~3.5" 或 "2-4"），切勿编造精确数值。
2. **区分轴标签与数据**：轴标签可能包含复杂的数学符号（如 $pIC_{50}$, $-\log K_i$），请完整保留。
3. **多面板处理**：若图像包含多个子图（Panel A, B, C），请按面板分别提取。
4. **无分子信息的数据点**：`reference` 与 `mol_id` 均填 `null`。
5. **严禁幻觉**：图像模糊无法确认时填 `"Unclear"` / `null`。
6. **仅输出 JSON**：不得包含任何解释文字。

## 💻 输出格式 (Structured JSON)

```json
{
  "figure_context": "图注标题或描述",
  "plot_type": "图表类型 (如 Scatter, Bar, Line, Bubble)",
  "axes": {
    "x_axis": {
      "label": "X 轴标签 (如 LogP)",
      "unit": "单位 (如 无单位, nM, %, 若无单位填 null)"
    },
    "y_axis": {
      "label": "Y 轴标签 (如 Metabolic Stability)",
      "unit": "单位"
    },
    "secondary_axis": {
      "label": "辅助轴标签 (若有, 否则 null)",
      "unit": "单位",
      "encoding": "编码方式 (如 bubble size, color intensity)"
    }
  },
  "data_points": [
    {
      "reference": "分子原文指代符 (如 Compound 3, Lead 1)，无则 null",
      "mol_id": "分子结构 bbox 右上角蓝色 index (数字)；图中无分子结构式时填 null",
      "x_value": "X 轴数值 (如 2.5, ~3, 若无法精确读取给估计值)",
      "y_value": "Y 轴数值",
      "secondary_value": "辅助维度数值 (若有, 否则 null)",
      "annotation": "数据点旁的额外标注 (如 Hit, Lead, Optimized, 若无填 null)",
      "group": "所属分组 (如 Series A, Generation 1, 若有分组)"
    }
  ],
  "trend_info": {
    "trend_line_equation": "趋势线方程 (若有, 否则 null)",
    "r_squared": "R^2 值 (若有, 否则 null)",
    "regions": [
      {
        "region_name": "区域名称 (如 Drug-like space, Target window)",
        "boundary_description": "边界描述 (如 LogP 2-4, MW < 500)"
      }
    ]
  },
  "legend": [
    {
      "symbol": "图例符号描述 (如 Blue circle, Red triangle)",
      "meaning": "图例含义 (如 Series A, Active compounds)"
    }
  ]
}
```

> **取值规则**：`data_points[].mol_id` = 该数据点对应分子结构检测框右上角的**蓝色数字 index**（图中无结构式则填 `null`）；`data_points[].reference` = 论文/图注中的文字指代符或简称，有结构时与 `mol_id` 同时记录。
