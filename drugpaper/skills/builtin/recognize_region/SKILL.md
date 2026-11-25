---
name: recognize_region
description: 重新识别指定分子框
---
# recognize_region

## 何时使用
params.box_ids 为当前图的 0-based mol_id 数组；仅用于识别失败框。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零 LLM 调用，调用已声明 MolParser 能力。
