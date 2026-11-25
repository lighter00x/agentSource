---
name: rebind_labels
description: 重新读取错误的编号与分子绑定
---
# rebind_labels

## 何时使用
只修正已存在且被核对争议的绑定字段；不能凭正文发明图内编号。

## 何时不用
不可访问其他文档或覆盖人工锁；没有可核对证据时不得臆造结果。

## 输入
当前 node_ref、检查码、closed review 与上述 params。

## 输出
remediation_action 审计记录；更正由固定实现执行，再校验。

## 代价与限制
一次定向 VLM + 一次 verify；至少保留两次调用预算。
