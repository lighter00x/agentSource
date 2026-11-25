---
name: measurement_map
description: 映射原始表格测定与条件。
---
# measurement_map

## 何时使用
已完成TableGrid转写。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
measurements及声明事实；保留来源、状态、错误。

## 代价与限制
解析单位、比较符、误差和脚注；保存原始值及行证据；metric_map_fallback只映射未知指标语义。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
