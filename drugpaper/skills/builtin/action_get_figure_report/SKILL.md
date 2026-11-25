---
name: action_get_figure_report
description: 读取同文档已提交图像报告
---
# action_get_figure_report

## 何时使用
params.node_ref 必须从当前文档的 document_figures 中选完整引用。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用，立即返回已提交报告或 pending，不等待其他 Agent。
