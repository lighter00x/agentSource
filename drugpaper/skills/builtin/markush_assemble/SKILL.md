---
name: markush_assemble
description: 按给定定义执行Markush拼接。
---
# markush_assemble

## 何时使用
用户/程序已明确提供母核与取代基定义。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
markush_assemble及声明事实；保留来源、状态、错误。

## 代价与限制
markush.substitute；原有兼容接口，不是图像解析器；新图表实例优先mol_backfill通用契约。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
