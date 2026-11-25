---
name: reclassify
description: 重新分类并选择正确的结构化链
---
# reclassify

## 何时使用
仅用于分类与图注/类型先验冲突；params 可为空。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
分类、结构化、verify 消耗剩余预算，不足则待复核。
