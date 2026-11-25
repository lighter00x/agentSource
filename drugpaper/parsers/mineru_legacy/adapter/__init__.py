"""layout.json (+ middle.json) → Document IR（设计文档 7.3 节，执行方案 S2.4a～S2.4d）。

处理顺序：units（识别单元）→ node_types（类型与标签）→ members（成员与图注）→ continuation（续段与行级增强）
→ sections（章节树）→ front_matter（前置信息）→ order（阅读顺序）→ quality（质检）。入口：`builder.build_ir`。
"""
