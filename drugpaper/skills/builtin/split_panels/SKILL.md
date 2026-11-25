---
name: split_panels
description: 根据有证据的边界拆分子图
---
# split_panels

## 何时使用
params.panels 为归一化 {x0,y0,x1,y1} 数组；最多8个。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
确定性几何投影；不把同类曲线的系列拆成 Panel。
