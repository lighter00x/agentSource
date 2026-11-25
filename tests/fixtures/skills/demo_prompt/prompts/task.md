## 任务
给下面的节点打一个标签。

节点：{{ target.ref }}（{{ target.type }}）
图注：{{ target.caption or "（无）" }}
{% if inputs.hint is defined %}提示：{{ inputs.hint }}{% endif %}
