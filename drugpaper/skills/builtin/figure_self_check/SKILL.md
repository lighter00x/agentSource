---
name: figure_self_check
description: 复核结构、数值、曲线与内容。
---
# figure_self_check

## 何时使用
已完成结构化与程序检查后。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
figure_review及声明事实；保留来源、状态、错误。

## 代价与限制
原图/叠加图对封闭checklist逐项检查；每项给证据，缺项/不确定为unsure；不能自证化学计算正确。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
