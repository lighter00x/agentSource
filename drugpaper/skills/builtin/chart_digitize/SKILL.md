---
name: chart_digitize
description: 用于性质曲线与统计图，转写坐标、曲线、图例及明确的分子对应。
---
# chart_digitize

## 何时使用
性质曲线与统计图；先完成检测、识别和编号图。

## 何时不用
其他图类或无证据的化学推导；含糊交复核。

## 输入
当前用户图像、图注和mol_id；视觉模型不读识别SMILES。详见包内 references/molecular-workflow.md。

## 输出
ID-only字段及证据；随后mol_backfill完成结构。x_axis/y_axis、plot_area、series.mol_id/reference、molecules。

## 代价与限制
按manifest预算/白名单执行。字段不明先读 molecular-workflow.md#本类步骤、#失败与复核；有效编号不等于化学正确。
