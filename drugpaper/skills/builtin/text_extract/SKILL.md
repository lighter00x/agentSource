---
name: text_extract
description: 提取正文中的化合物、测定和优化叙述，保留原文证据。
---
# text_extract

## 何时使用
抽取摘要、结果、讨论、结论或实验段落中的事实。

## 何时不用
图像解析、参考文献、缺少原文的节点。不能代替整篇阅读或化学结构认证。

## 输入
当前正文节点 target_ref，inputs={}。自动读取 text_raw、section_role。

## 输出
text_extraction 产物；char_span 为规范化文本偏移，evidence.char_span 为原文偏移。证据不符的条目丢弃并标 needs_review。

## 代价与限制
按段处理，超长段落按句切块，每块最多一次调用、2800 tokens，共最多12次；引言仅规则提及，未知章节跳过。通过 artifact_inputs 读取完整产物。
