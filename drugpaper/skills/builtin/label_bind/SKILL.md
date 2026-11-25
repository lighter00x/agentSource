---
name: label_bind
description: 生成邻近编号几何候选。
---
# label_bind

通过确定性代码和声明的 Tool 实现 P4 化学能力，使用当前用户文档材料。节点及事实随 Artifact 原子入库，E-SMILES 原文不参与规范化。

分子编号为标注图的 reading_index，不能用检测返回列表位置猜测识别结果；空检测需要后续 FigureAgent 回退。几何 label_bind 只生成候选，最终绑定需由结构化抽取确认。复杂图像抽取在 P5 实现。
