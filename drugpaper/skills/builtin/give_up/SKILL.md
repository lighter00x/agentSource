---
name: give_up
description: 停止补救并保留人工待复核项
---
# give_up

## 何时使用
证据无法确定、预算不足、建议相互冲突时使用；params 可为空。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用；保留所有抽取、检查与 open_questions。
