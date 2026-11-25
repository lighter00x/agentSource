## usage
当前执行契约在本包 molecular-workflow.md。通过 read_skill_resource(name="measurement_map", section="molecular-workflow.md#本类步骤") 读取本类步骤；再按需读取 #字段与实例、#执行与交付、#失败与复核、#验收清单。不能省略确定性回填与内容验收。Schema、manifest.tools 和预算是硬边界；历史参考不覆盖这些约束。

调用输入必需字段为 `grid`；可通过 `artifact_inputs={"grid":{"artifact_id":"产物ID","path":[]}}` 引用完整产物。mol_backfill产物的抽取位于path=["payload"]。
