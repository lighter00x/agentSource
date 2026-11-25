# 分子检测 Tool：调用与多用户并发

2026-10-01。实现见 `drugpaper/detection/`、`drugpaper/tools/builtin/molecule_detection.py`。设计与旧代码问题见 `molecule-detection-tool-design.md`。

## 调用

4 个 Tool 已自动注册到 SkillServices 的能力索引，支持现有发现、加载、下一轮调用和 Skill 工具白名单。

| Tool | 输入 | 输出 |
|---|---|---|
| molecule.detect | target_ref、parent_region_index=0、preset=default/high_recall | status、result_ref、image_ref、count、cached、warnings、provenance |
| molecule.detections | result_ref、offset=0、limit=1～10 | 框、置信度、类别、冻结编号、next_offset、图像尺寸与父 region 序号 |
| molecule.crop | result_ref、label_ids（最多 10 个） | 保持请求顺序的 detection_id/label_id/裁剪图片引用 |
| som.render | result_ref | 使用同一 label_id 的标注图片引用 |

target_ref 必须为 `{document}/r{revision}/{node}`；跨页图通过 parent_region_index 指定图像区域。局部框使用 xyxy、归一化 [0,1]，原点为输入图左上。检测框和按框宽高扩展后的裁剪框分别保存，不自动把高覆盖的多框合并成整图。渲染使用 pad=0，因此局部框可按保存的 parent_region 映射到页面。

返回的编号只在冻结的检测结果中有意义，不等于正式领域 mol_id。输入图片或参数改变会生成新 result_key。no_detections 是成功执行；服务缺失、超时或坏图明确失败。

模型可见结果保持精简，全部框保存在用户结果资产中，通过分页读取；最多 10 个框/页且输出预算支持该大小，避免通用字符截断破坏结构。Agent 不需要传文件路径或长 base64。Python Skill 可直接通过 ctx.blobs 读取用户图片资产，再调用已有 molparser.recognize；P4 的正式识别结果对齐、分子投影仍待完成。

示例（Python Skill 的白名单中声明对应 Tool）：

```python
result = await ctx.tools.call("molecule.detect", target_ref=ctx.target_ref)
page = await ctx.tools.call("molecule.detections", result_ref=result["result_ref"])
crops = await ctx.tools.call("molecule.crop", result_ref=result["result_ref"], label_ids=[d["label_id"] for d in page["detections"]])
marked = await ctx.tools.call("som.render", result_ref=result["result_ref"])
```

完整代码服务接口支持 `await services.detector().detect(user_id, image_bytes, preset)`；Backend 可注入替身或其他实现。多图片使用逐项请求并保留调用方输入编号，不暴露旧四元组或 Tensor。

## 并发与用户隔离

- 模型由专用 ThreadPoolExecutor（1 个线程）延迟加载并复用。用户轮转排队，总等待队列默认 16，每用户运行+等待任务默认最多 2；队列满与用户超配额立即返回可重试错误。
- 排队期限 30 秒，总等待/执行期限 120 秒，可配置。请求超时或取消不会释放正在执行的推理槽位；等待中的取消任务跳过推理。关闭服务停止接收，拒绝等待任务，等待活动推理结束再释放后端。
- 队列任务数、单图字节数（12MB）、像素数（1600 万）、框数（256）和缓存项数（64）均有上限。请求 JSON 在解码前有字节上限，包括分块传输。
- 图片、检测结果、裁剪和标注按 ctx.user_id 使用独立 BlobStore；每次 Tool 调用显式核对文档所有者与会话文档。猜到 result_ref 不能跨用户读取；即使结果文件被拷贝，owner 和文档校验仍生效。
- 缓存按用户、图片摘要、权重 SHA256、后端/设备、有效参数、扩框比例及后处理版本区分，返回深拷贝。权重变化后当前 Worker 拒绝继续使用旧模型，需重启。Skill 若声明 molecule.detect 且未提供 extras.tool_fingerprints 的后端版本，则跳过 Artifact 缓存，由检测 Tool 自己复用正确版本结果。
- 用户轮转不抢占已运行推理，长任务仍会占用当前槽位。设置像素与排队上限，负载持续过高时应增加独立 GPU Worker 并稳定分片，而不是提高单模型并发。

## 多进程部署

普通应用不必安装 YOLO：默认依赖只包含图片处理；本地推理安装 `uv sync --extra yolo`，CPU/CUDA 的 torch 构建按运行环境选择。

推荐多 API 进程使用一个独立检测 Worker：

```bash
# 专用推理环境：设置真实权重路径、设备与服务令牌。
export YOLO_MODEL_PATH=/absolute/path/moldet_v2_yolo11n_640_general.pt
export YOLO_DEVICE=cpu
export YOLO_SERVICE_TOKEN='replace-with-service-token'
uv run --extra yolo uvicorn drugpaper.detection.api:create_app --factory --host 127.0.0.1 --port 8010 --workers 1

# 应用环境：所有 API Worker 指向同一个检测服务。
export YOLO_BACKEND=remote
export YOLO_BASE_URL=http://127.0.0.1:8010
export YOLO_SERVICE_TOKEN='replace-with-the-same-service-token'
uv run drugpaper serve
```

单个检测 Worker 保证该 Worker 的全局配额与模型复用。必须保持 --workers 1；启动多个检测进程会各自拥有模型、队列和缓存。local 适合单应用进程与开发；它不提供跨应用进程的全局队列保证。跨主机部署由服务网络层提供受控连接。

POST /detect 使用服务 Bearer token；user_id 由可信应用从已验证的 ToolContext 传入，不由终端用户随意指定。当前项目完整 HTTP 登录认证仍按 P7 计划，不能把服务 token 分发给终端用户，也不能把内部 user_id 参数当作登录认证。

错误 data 包含 message、retryable，error 使用 E_USER_BUSY、E_QUEUE_FULL、E_TIMEOUT、E_INVALID_IMAGE、E_MODEL_NOT_READY、E_MODEL_CHANGED、E_OOM、E_RESULT_COUNT_MISMATCH、E_INVALID_BOX、E_BACKEND_UNAVAILABLE 等稳定代码；remote 配置错误不会静默回退本地。

## 验证

单元测试：`uv run pytest tests/unit/detection tests/unit/skills/test_runtime.py -q`。包含用户轮转、队列满、取消、推理超时占用、关闭、配额、跨用户资产拒绝、分页/编号/裁剪、两个应用客户端共用 Worker 及认证/请求大小限制。

真实权重验收：

```bash
uv run --extra yolo python scripts/verify_molecule_detection.py --model /absolute/path/model.pt --images /absolute/path/image1.jpg /absolute/path/image2.jpg
```

2026-10-01 两张旧项目样例真实 CPU 复验：两用户共 4 次请求，框数 5/31/5/31，模型仅加载一次，最大同时推理为 1，重复调用命中缓存。结果与可复核图片见 `samples/outputs/molecule-detection/20261001-202449/`。检测精度与领域分子投影不由这次封装验收代替。

线程超时无法硬取消模型执行；资源一直保留至返回。需要硬终止或从卡死推理恢复时，重启专用 Worker 进程；当前不声称实现 GPU 任务硬抢占。

最终全量回归为 374 项通过；另已用两个独立应用进程访问同一个临时真实权重 Worker 验证共享队列、用户结果区分和缓存。证据见样例输出目录的 completion.md 与 cross_process.json。Worker HTTP 契约见 molecule-detection-worker.openapi.json；结果 Schema 见 molecule-detection.schema.json。

实现依据：[Ultralytics 线程安全推理说明](https://docs.ultralytics.com/guides/yolo-thread-safe-inference)与[结果对象接口](https://docs.ultralytics.com/reference/engine/results)。后端只在一个专用线程创建和使用模型，返回结果转换为独立 Python 数据。


## 当前本机部署

2026-10-01 已部署常驻 API（2 Worker）与共享检测服务（1 Worker），应用使用 remote，检测服务使用独立 CUDA 环境和物理 GPU 0。权重位于项目 data/models，内部令牌通过私有部署配置注入。当前部署与复验记录见 `deploy/README.md`，此前 CPU 验收仅作为封装历史证据。
