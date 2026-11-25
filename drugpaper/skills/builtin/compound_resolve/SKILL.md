---
name: compound_resolve
description: 归一化正文编号、别名、立体变体和 OPSIN/OCSR 结构候选，保留冲突与角色初判证据。
---
# compound_resolve

目标是当前用户整篇文献，inputs={}；可提供 scope=[compound_id] 只重算已有化合物。
代码读取当前 revision 的有效候选、标签和有原文指纹的 text_extraction 提及，零模型调用。
优先级为人工、OPSIN 分子式一致、OCSR 分子式一致/高置信、未验证 OPSIN、Markush、低置信 OCSR、分子式不一致 OPSIN、LLM；人工锁保持。
OPSIN WARNING 和无效/Markush 候选不参与采纳。分子式冲突、结构冲突、别名歧义均保留证据。
角色是初判：模型提及或摘要/结论重复编号提示 hit/lead/final，统一标 needs_review，最终路径交给后续 ReaderAgent。
正文、结构或台账快照在提交前改变则整份回滚。过期显式结构 ID 不替换为同节点另一候选。
返回 compound_resolve 产物与状态；没有结构的编号也保留，不能凭编号制造结构。
详情见 references/usage.md。
