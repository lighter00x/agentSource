---
name: fix_unit
description: 应用核对证据支持的单位更正
---
# fix_unit

## 何时使用
params.paths 指向单位/表头争议字段；只使用 closed review 建议。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用后 verify；不接受任意猜测的换算参数。
