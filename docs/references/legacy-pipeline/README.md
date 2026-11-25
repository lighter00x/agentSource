# 旧流水线复用记录

来源：`/home/xq/source/paper`。核查范围包括处理引擎、文档/版面解析、YOLO 检测与裁剪、MolParser 识别、模型客户端、分子数据结构、mol_id 回填、Markush 拼接及其测试、prompt_v2 的分类/准入/各图像类型规则和批处理入口。

P3 优先完成能力注入与组合执行。旧流水线不会作为新工程运行依赖，也不导入其全局 config、GPU 模型、logger 或批处理引擎。

| 资产 | P3 的处理 | 后续阶段 |
|---|---|---|
| E-SMILES/Markush 拼接 | `chem/splicer/vendor.py` 保留核心实现和 Apache-2.0 许可；适配层保留 prime、移除显式 H、限制原子组展开、统一列表与错误码；封装 `markush.substitute` Tool 与调用它的 Python `markush_assemble` Skill | P4 完成识别结果和 RGroupAssignment 的领域连接 |
| 分子角色 | `esmiles.infer_role` 保守推断；普通 SMILES 保持 unknown | P4 加入结构候选与上下文角色 |
| mol_id 回填 | `mol.backfill` 支持单数与复数编号，不原地修改输入，不把布尔值当整数编号，不接受未知编号 | P4/P5 增加分类 Schema 中取代基列等领域规则 |
| MolParser | 复用已接入的异步网关，注入 health/recognize Tool；图片以 base64 输入，保留结果索引与 E-SMILES | P4 完成检测、分批对齐、识别后处理 |
| 公共提示 | 保留证据边界、只引用真实结构框的 mol_id、数值/单位原文、缺失填 null、不编造等规则 | 所有领域 Skill 复用公共片段 |
| 分类和准入 | 13 类及单/多路线修正规则；准入统一 JSON，低置信度继续处理；3 个回放用例/包 | 扩充论文集和领域专家标注 |
| 各类图像抽取 | 原 prompt_v2 专用文本在本目录归档为参考，不自动注入上下文；routing.yaml 声明 P4/P5 的 planned Skill 链 | 逐类实现 Python Skill，组合检测/识别/标注/一次结构化/校验/回填 Tool；复杂图像不依赖单次 prompt 完成 |
| YOLO/裁剪/标注 | 本阶段只记录接口要求，未加载旧 GPU 全局模型 | P4 修正按框宽高扩框、返回 conf/cls、面积并集与输入编号 |
| 全文一次性总结、LLM 自评分 | 不迁移到运行链路 | P6 使用证据与事实层，质量由确定性检查计算 |

参考文本保留旧规则和字段，仅作迁移资料；其中旧版多路线判定矛盾不得直接照搬。本文档不是 P4/P5 已完成的声明。
