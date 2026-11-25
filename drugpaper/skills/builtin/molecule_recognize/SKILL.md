---
name: molecule_recognize
description: 识别裁剪结构并保存候选。
---
# molecule_recognize

## 何时使用
已有检测节点需识别/重识别。

## 何时不用
不能替代其他图类视觉抽取或凭空补化学事实。

## 输入
target_ref指当前图像，inputs={}；上游检测材料由服务端加载，不传detections或图像base64。

## 输出
ocsr_result及声明事实；保留来源、状态、错误。

## 代价与限制
molparser.recognize；按input_index对应节点；原始E-SMILES和校验失败也保存，角色保守判断。详细步骤见包内 molecular-workflow.md#本类步骤、#验收清单；执行完成不代表内容通过。
