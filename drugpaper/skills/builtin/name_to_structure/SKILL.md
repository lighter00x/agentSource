---
name: name_to_structure
description: 解析实验部分名称与编号，保留 OPSIN 结构候选和 HRMS 校验。
---
# name_to_structure

目标是当前文献实验章节的 paragraph/list，inputs={}，不需要图像。
代码切分段首“系统名称 (编号).”，调用平台配置的 OPSIN 服务，零模型调用。
成功时提交 source=opsin 候选与标签证据，分子式按中性分子或显式加合离子比对。
warning、无效结构、分子式不一致、缺少 HRMS、服务不可用均标 needs_review。
没有名称或非实验章节返回 skipped。不得根据论文标题猜实验名称；不得自行修改结构或编号。
输出是 name_to_structure 产物 ID 与状态；同一事务写候选和标签，重跑保留历史。
原文、章节或 revision 改变必须重跑。缺失服务不阻断其余正文流水线。
详情见 references/usage.md。
