---
name: redetect
description: 重新检测漏掉的分子框
---
# redetect

## 何时使用
适用于 C_NO_BOX/C_UNREF_BOX；params 可为空，使用 high_recall。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
零模型调用；更新框后重新识别与校验。
