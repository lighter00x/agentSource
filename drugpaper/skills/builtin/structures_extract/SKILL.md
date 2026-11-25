---
name: structures_extract
description: 用于分子展示与优化图，转写明确图示分子、母核及组件。
---
# structures_extract

## 何时使用
分子展示与优化图；先完成检测、识别和编号图。

## 何时不用
其他图类或无证据的化学推导；含糊交复核。

## 输入
当前用户图像、图注和mol_id；视觉模型不读识别SMILES。详见包内 references/molecular-workflow.md。

## 输出
ID-only字段及证据；随后mol_backfill完成结构。molecules、compound_instances、variants、substituents。

## 代价与限制
按manifest预算/白名单执行。字段不明先读 molecular-workflow.md#本类步骤、#失败与复核；有效编号不等于化学正确。
