---
name: pharmacophore_model_extract
description: 用于药效团与分子叠合图，转写真实分子参考和药效团特征。
---
# pharmacophore_model_extract

## 何时使用
药效团与分子叠合图；先完成检测、识别和编号图。

## 何时不用
其他图类或无证据的化学推导；含糊交复核。

## 输入
当前用户图像、图注和mol_id；视觉模型不读识别SMILES。详见包内 references/molecular-workflow.md。

## 输出
ID-only字段及证据；随后mol_backfill完成结构。molecules、reference_molecules、overlay_info；compound_instances。

## 代价与限制
按manifest预算/白名单执行。字段不明先读 molecular-workflow.md#本类步骤、#失败与复核；有效编号不等于化学正确。
