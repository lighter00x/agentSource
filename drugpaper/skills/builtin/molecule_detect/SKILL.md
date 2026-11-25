---
name: molecule_detect
description: 检测分子并建立稳定节点。
---
# molecule_detect

## 何时使用
当前图像首次检测或有新证据需重检。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
当前用户文档节点与上游产物；不能跨图/跨用户混用mol_id。

## 输出
molecule_boxes及声明事实；保留来源、状态、错误。

## 代价与限制
molecule.detect；保存裁剪与稳定reading_index；不将检测框当分子真实性证明。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
