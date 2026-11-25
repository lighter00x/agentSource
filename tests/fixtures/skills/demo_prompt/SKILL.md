---
name: demo_prompt
description: 测试用：给图表节点打一个标签。
---

## 何时使用
测试 prompt 型 Skill 的加载、执行与缓存。

## 何时不用
生产环境。

## 输入
node_ref；hint（可选）。

## 输出
demo_label：label、confidence。

## 代价与限制
1 次模型调用。
