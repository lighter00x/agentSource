---
name: recalibrate_axes
description: 按已核实刻度重新标定坐标轴
---
# recalibrate_axes

## 何时使用
适用于轴刻度残差、对数尺度和回绘异常；优先使用核对建议。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
确定性 CV + verify；不能猜测缺失刻度。
