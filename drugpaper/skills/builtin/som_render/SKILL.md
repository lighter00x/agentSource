---
name: som_render
description: 生成供视觉理解的分子编号图。
---
# som_render

## 何时使用
检测节点存在且需视觉结构化。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
target_ref指当前图像，inputs={}；上游检测材料由服务端加载，不传detections或图像base64。

## 输出
som_image及声明事实；保留来源、状态、错误。

## 代价与限制
使用代码绘制稳定reading_index；编号仅图像辅助，不改源图或向模型注入SMILES。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
