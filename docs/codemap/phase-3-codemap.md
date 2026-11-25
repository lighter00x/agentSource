# P3 代码地图：Skill 运行时与渐进式披露

范围：v0.3.0。冻结契约见 `docs/interfaces/skills-and-capabilities.md`；旧流水线复用记录见 `docs/references/legacy-pipeline/README.md`。

| 模块 | 职责 |
|---|---|
| skills/protocol.py、loader.py | 严格 Manifest/Result、包校验、提示闭包哈希、资源索引 |
| skills/registry.py、watcher.py | 版本状态、不可变快照、回放测试、作用域、监听和 Redis 通知 |
| skills/execution.py、runtime.py、materials.py | prompt/Python 共用执行、预算、材料指纹、验证、缓存、原子提交 |
| skills/bootstrap.py | Services 注入、文档图片材料、共享 Tools、模型、索引和 SkillTool |
| capabilities/index.py、router.py | L0/L1 索引、可见性、确定性预选与预算 |
| capabilities/assembler.py、meta_tools.py、toolset.py | 分层提示、5 个元工具、动态 L2、L3、会话 LRU 和披露记录 |
| agent/runtime.py、qa.py | 下一轮加载约束、能力状态 checkpoint、P1/P2 问答接入 |
| gateway/task_router.py、openai_compat.py | 任务映射、Responses、领域工具名传输映射 |
| chem/splicer/、chem/backfill.py | 旧 Markush 核心及许可、边界适配、确定性回填 |
| tools/builtin/chem.py、molparser.py | 6 个基础计算/服务 Tools |
| skills/builtin/ | classify、qualify、可选 describe、Python markush_assemble；每包 3 个回放用例 |
| skills/routing.py、config/routing.yaml | 13 类图像路由、planned、模板、合并和 D38 |
| api/skills.py、persistence/models.py、migrations/0003～0005 | 目录 API、管理员 reload、版本/披露/提案表与唯一 active 约束 |
| scripts/verify_phase_3.py、tests/unit/、tests/integration/ | 可复现离线/真实分类验收及回归 |

执行链：注册包 → 校验与回放 → 激活快照 → 索引发现/预选 → 下一轮加载 Tool Schema → SkillRuntime → Tool/模型/代码组合 → 校验 → 原子提交 → 事件。

完整验收证据在 `samples/outputs/phase-3/20261001-183351/completion.md`。参考标注由 Codex 视觉复核，分类基线并非领域专家签核。

## v0.3.1 检测 Tool 扩展

`detection/backend.py`：可注入 Backend、真实 YOLO；`service.py`：用户轮转队列与模型生命周期；`remote.py`：共享 Worker 客户端；`api.py`：内部令牌与请求上限。`tools/builtin/molecule_detection.py`：4 个 Tool、文档归属与用户资产隔离、分页、裁剪和标注。Services 提供检测实例和用户存储，bootstrap 自动注册。契约与运行边界见 `docs/interfaces/molecule-detection-tool.md`，真实证据见 `samples/outputs/molecule-detection/20261001-202449/completion.md`。
