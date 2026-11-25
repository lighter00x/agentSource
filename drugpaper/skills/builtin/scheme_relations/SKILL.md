---
name: scheme_relations
description: 保存有证据的反应、优化与选择关系。
---
# scheme_relations

## 何时使用
合成图完成mol_backfill后。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
scheme_relations及声明事实；保留来源、状态、错误。

## 代价与限制
使用最终回填extraction；区分反应/优化/选择；未解系列不生成具体反应。验证当前箭头的variant_links并保存product_structure_id；错误隔离且保留。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
