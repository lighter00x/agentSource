---
name: action_search
description: 在当前文档检索相关正文证据
---
# action_search

## 何时使用
params 为 search Tool 参数，缩小 query 与结果数；仅当前文档。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用，manifest 白名单限制 search。
