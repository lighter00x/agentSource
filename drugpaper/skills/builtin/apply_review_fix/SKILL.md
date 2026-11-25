---
name: apply_review_fix
description: 应用封闭核对中明确的更正值
---
# apply_review_fix

## 何时使用
params.paths 可选，仅能应用 incorrect 的 suggested_value。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用后 verify；保留 source=vlm_review，不覆盖人工锁。
