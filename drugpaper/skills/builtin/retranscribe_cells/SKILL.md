---
name: retranscribe_cells
description: 从原图重新转写争议单元格
---
# retranscribe_cells

## 何时使用
params.cells 为 rows[r][c] 或 .text 路径；只修正现有争议字段。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
一次定向 VLM + 一次 verify；不改其他单元格。
