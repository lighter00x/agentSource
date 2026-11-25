---
name: panel_split
description: 分割面板并生成子图节点。
---
# panel_split

## 何时使用
multi_panel且面板框可读。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
panels及声明事实；保留来源、状态、错误。

## 代价与限制
只用归一化panel bbox；不复用父图mol_id；子FigureAgent独立执行图类链。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
