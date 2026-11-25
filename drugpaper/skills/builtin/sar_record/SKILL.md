---
name: sar_record
description: 生成SAR完整结构与逐行指标。
---
# sar_record

## 何时使用
SAR TableGrid具有明确compound_instances。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
sar_records及声明事实；保留来源、状态、错误。

## 代价与限制
调用mol.deliver复算实例；按row_index与测定evidence连接properties；要求覆盖每个非空数据行。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
