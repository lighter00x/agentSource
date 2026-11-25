---
name: reslice_table
description: 缩小表格行带后重新转写
---
# reslice_table

## 何时使用
适用于行数不符或表头跨度异常；params 可为空。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
切片逐次计数；受四次补救调用和180秒约束。
