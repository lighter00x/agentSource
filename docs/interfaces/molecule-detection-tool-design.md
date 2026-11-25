# YOLO 分子检测 Tool 封装方案

2026-10-01，基于旧项目实际代码审阅。封装方案已实现并通过真实 CPU 权重复验；当前调用与并发边界见 `molecule-detection-tool.md`。正式分子投影仍属于后续 P4。

## 现有实现与复用边界

已阅读 `/home/xq/source/paper/detection_yolos/yoloManger.py`、`utils_yolo.py`、`Processor/paperEngeine_v2.py` 中检测/裁剪/识别链，以及 `tests/test_yolo.py` 和配置。

可复用：分子专用权重加载、RGB 批量推理、归一化框、坏图保留输入位置、图像标注思路、结果数量不符时避免直接错配。迁移到新服务时剥离旧全局 config、处理引擎、目录命名与 CSV 写入逻辑。

必须修正：

- process_images 的四元组包含始终为空的 all_mols；接口声明与实际返回不一致。新接口使用类型化对象。
- 框面积相加会重复计入重叠区域。即使改为面积并集，高覆盖率也不能证明只有一个分子；禁用自动合并整图，保留原始框，整图补救由 Skill 显式决定。
- 扩框使用 0.95*x 与 1.05*(x+w)，扩框量随位置变化。改为 dx=padding_ratio*框宽、dy=padding_ratio*框高，再裁到图像边界。
- 标注图的框与实际裁剪框必须使用同一几何结果；完整保存 raw_bbox 和 crop_bbox，分别用于证据与识别输入。
- 旧输出遗漏 conf 与 class，编号来自模型返回顺序。保留置信度/类别，编号在一次检测结果中冻结，后续筛选和失败均不得重新编号。
- process_images 用 zip(results, images)，可能静默丢弃尾部。逐层验证输入输出数量与映射，明确报告数量不符。
- 当前每轮流水线加载并卸载模型。新服务延迟加载并复用实例，GPU 推理由受控队列串行或按后端确认的并发能力执行。
- 包含的小框区域置白可能擦去化学信息；作为显式、可追踪的裁剪选项，默认关闭，保存未修改的原图。

## 服务、Tool 与 Skill

DetectorBackend 接收解码后的图像和服务配置，返回纯 Python 类型的像素 xyxy/conf/class；不泄露 ultralytics Results、Tensor 或 PIL 对象。可注入 FakeBackend，未来替换远程服务时维持相同契约。

MoleculeDetectionService 负责模型生命周期、权重摘要、推理队列、输入输出校验、框规范化及稳定编号。后端实现按配置显式选择 local 或 remote，故障不得自动切到另一种结果不可追溯的后端。

`molecule.detect` Tool 负责输入材料解析、文档可见性检查、调用服务、转换 ToolResult 与追踪。通过 Services 延迟提供服务，在 SkillServices 中注册，manifest.tools 白名单和现有能力披露机制照常生效。Tool 名称与业务产物/Skill 名称不同：Skill `molecule_detect` 调用 Tool `molecule.detect`，将结果提交为检测 Artifact。

裁剪与标注建议拆为 `molecule.crop`、`som.render` Tools，由共享的几何函数实现。识别继续复用 MolParser 网关；当前 molparser.recognize 仍是 base64 基础接口，P4 增加面向 blob_ref、检测编号和逐项状态的适配。纯检测 Tool 不修改 IR，不创建正式分子或结构事实。

## 模型可见输入

优先提供 `target_ref`、`parent_region_index` 和可选 preset（default/high_recall）。region 对跨页图必须明确选择；单 region 图可默认为 0。代码型 Skill 可使用已解析的 ImageMaterial；聊天层不发送长 base64，不选择任意本地路径、权重路径或 GPU。

服务配置提供 conf、iou、imgsz、device、最大输入像素、最大检测数和批大小。默认值来自项目配置；preset 所用的最终数值必须记录。若开放高级参数，必须带取值边界且参与指纹，不让 LLM 调整设备和权重。

ImageMaterial 保存 blob_ref、像素宽高、父 region、渲染 DPI 和实际裁剪范围。后续 local_bbox 以实际检测图为坐标系，经明确变换映射到父 region 和页面。渲染 padding 会改变图像覆盖范围，不能直接把带 padding 图片的归一化框当作未扩展父 region 内坐标。

## 输出契约

每张输入图返回独立对象，包含 input_index、image_ref、target_ref、parent_region_index、status、detections、warnings、error 和 provenance。status 区分 completed、no_detections、failed；无检测属于成功执行，不应与服务错误或坏图等价。

单项 detection 示例：

```json
{
  "detection_id": "det_<result_key>_0001",
  "label_id": 1,
  "bbox_norm_xyxy": [0.12, 0.24, 0.38, 0.57],
  "confidence": 0.91,
  "class_id": 0,
  "class_name": "molecule",
  "crop_bbox_norm_xyxy": [0.10, 0.22, 0.40, 0.59]
}
```

坐标明确为相对输入图的 [x0,y0,x1,y1]，左上原点、归一化到 [0,1]，与新项目 BBox 一致；不继续暴露语义不明的 xywh。provenance 包含权重 SHA256、后端及版本、实际参数、输入图片摘要、后处理版本、耗时和是否缓存。

label_id 是标注图中给 VLM 看的短编号，detection_id 是程序关联键；它们不等于已批准的领域 mol_id。排序可以约定为确定性的几何顺序，并用完整几何/类别/置信度打破并列，但不能保证不同权重、阈值或硬件推理下编号恒定。一次检测结果内所有 Tool 共用冻结映射；重新检测生成新结果，不沿用旧 mol_id。

标注图、裁剪图存入 BlobStore，只返回引用。聊天返回摘要/少量检测对象和可读取的完整结果引用；Skill 从结构化结果或结果资产读取全部数据。不能依赖当前 ToolRegistry 的字符截断来处理大量检测框，否则结构化 data 会丢失。基础实现可以先限制目标为单图和有限结果，分页/资产读取能力与之一起提供。

## 错误、并发与缓存

批量内部接口保持输入长度：坏图占位失败，其他输入继续；服务级失败整体明确失败。标准错误至少区分 E_INVALID_IMAGE、E_MODEL_NOT_READY、E_BACKEND_UNAVAILABLE、E_TIMEOUT、E_OOM、E_RESULT_COUNT_MISMATCH、E_INVALID_BOX，并返回 retryable。模型未配置时不把空 detections 当作成功。

队列容量和等待超时有界；推理时间单独记录。asyncio.to_thread 不会在协程超时后自动停止 GPU 线程，必须保持真实任务占用直至结束，防止超时重试累积并发。需要硬取消时使用专用推理 Worker 进程。

缓存键覆盖图像摘要、权重摘要、有效参数、后处理版本和坐标变换信息。相同冻结结果复用编号；修改参数/权重必须失效。只有权重摘要和后端参数进入缓存，不能包含密钥。Skill 的 input_fingerprint 必须补入检测后端配置/结果版本，仅靠文本模型配置不足以保证检测失效。

## 接入后续图像 Skill

合成路线/SAR 等 Python Skill 组合：检测 → 按原始框裁剪 → MolParser 识别 → 共用编号绘制标注图 → 一次视觉结构化 → 编号与结构校验 → 分子回填。识别失败仍保留检测编号，结构为 null 并记录状态，不允许过滤失败项后按数组顺序配回。已有 Markush Tool 可用于证据充分的后续拼接。

Tool 结果的框可以作为 MoleculeProposal 来源；正式领域分子编号、角色、结构候选与人工审核由 P4 投影负责。高覆盖率、空结果和低置信度是 Skill 的可观察分支，不能在底层偷偷改变业务语义。

## 验收要求

单元测试使用 FakeBackend：空检测、坏图位于批次中间、数量不匹配、NaN/越界/零面积框、坐标变换、边缘扩框、重叠框不合并、编号冻结、识别部分失败对齐、不同图同名不冲突、缓存失效、队列/超时、模型仅加载一次、状态与错误码。

契约测试：真实 ToolRegistry 注入、Skill 白名单调用、跨文档访问拒绝、结果不会被字符截断破坏、标注编号可回查、资产引用可读取、提案几何满足 P3 校验。

真实权重验收使用已保存样例及补充图像：记录原图/标注图/裁剪图、检测数量、conf/class、权重/参数、冷启动和热调用耗时。人工复核漏检、误检及结构标签是否被裁掉。重点覆盖多分子合成路线、SAR 母核与取代基、整图单结构、无分子图及跨页图；单元测试通过不能替代这一步的模型质量验收。
