# 化合物归一

沿用 P4 化学事实表、人工锁、别名和立体父子关系，不需要额外迁移。

OPSIN 原始 SMILES 与 OCSR E-SMILES 均保留；RDKit 规范化只生成派生字段。
formula_match=false 降低优先级并生成公式冲突；formula_match=null 是未核验，不能视为匹配。
HRMS 校验只是元素组成比对，精确质量、同位素、盐和复杂加合物不在本次校验范围。
重复角色提示或明确 hit/lead 提及是 Reader 的候选台账，不是最终成药性结论。

事实投影与 Artifact 在同一事务；重新计算保存旧版本，当前编号 ID 保持。
服务故障、编辑、章节修改、换 revision、人工锁变更都有显式复核或回滚路径。
参考文档 docs/interfaces/full-text.md 与 docs/codemap/phase-6-codemap.md。
