---
name: apply_text_layer
description: 使用唯一几何命中的 PDF 文本修正
---
# apply_text_layer

## 何时使用
params.cells 为单元格路径数组；需要 bbox 和唯一可见文本 span。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用后 verify；标记 source=text_layer，歧义时拒绝。
