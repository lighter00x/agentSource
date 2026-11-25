# P3 冻结接口：Skill 与能力披露

2026-11-20 实现补充：以下需求说明保留确认时的基线；基础调试能力已通过独立 `/documents/{id}/workbench` 接口接入，详见 [监控文档的工作台接口](monitoring.md#基础工作台接口2026-11-20)与 [P8 最新交付记录](../dev-plan/execution/phase-08-execution.md#2026-11-20-基础版本实现与验证记录)。原冻结接口和完整阶段验收结论不变。

## 2026-11-20 局部 Skill/工具开发调试扩展（待实现）

本期需求见[总体设计](../drug-paper-agent-detailed-design.md) 13.7/17.6、[P8 执行方案](../dev-plan/execution/phase-08-execution.md)。不改变下文 P3 冻结接口；现有目录/资源索引/版本及全局 reload 不等于已经提供网页文件编辑和局部试运行。

- 编辑范围包含 SKILL.md、manifest、prompts、references、输入输出 Schema、执行脚本及相关公共工具实现代码；需新增文件内容读取、局部草稿保存、校验、试运行、差异/版本与主动全局发布能力。
- 局部覆盖绑定 user/document/revision/figure；引用的公共代码也按局部依赖版本装载，不能保存即覆盖全局源文件。局部版本可与全局版本比较，显式发布才影响之后的其他图表。
- 每次运行保存实际使用的包、代码与资源快照及哈希，缓存包含局部依赖；历史与正在运行的版本不随编辑/发布改变。现有进程内 Python 机制需要扩展才能实现代码作用域隔离。
- 单图解析默认自动分类和路由；指定 Skill 是额外调试操作。人工改图类要重新路由，版本与人工覆盖进入运行记录。
- 局部代码执行仍遵守工具白名单、归属与输入输出校验；校验失败保留错误，不替换有效版本。新增写/执行 API 经实现和测试后再冻结，本次只更新需求。

版本：0.3.0，2026-10-01。类型定义以 `drugpaper/skills/protocol.py` 与导出的 `skill-manifest.schema.json` 为准；HTTP 契约见 `openapi-p3.json`。

## 包与生命周期

目录包含 `SKILL.md`、`manifest.yaml`、输入/输出 JSON Schema、prompts 或 scripts、references、tests。支持 prompt 与可信进程内 Python；graph、沙箱和 user 作用域留待后续阶段。Python 包支持相对导入，不应导入旧流水线全局配置或 GPU 模型。

注册经过 discovered → validating → testing → staged → active；错误包进入 invalid，不替换当前 active。`enabled: false` 保持 staged。builtin/system 分别解析，同名 system 覆盖需 `override: true`。管理员可激活旧的 staged 版本回滚。删除源目录会停用对应包。

包与使用到的公共提示片段共同决定 content_hash，加载后保存不可变快照。文件监听会重新校验、回放并激活；Redis `skills:changed` 通知其他 Worker 清缓存。运行中的任务持有原版本，不随热更新改变。API 服务自动启动监听；其他进程可显式使用 SkillWatcher。

## 执行与提交

`SkillRuntime.run(skill_name, target_ref, inputs, ctx, force=False)` 返回 SkillOutcome。SkillContext 注入受 manifest.tools 白名单约束的 ToolCaller、按 task 路由的模型、Materials、预算及追踪信息。prompt 与回放用例使用相同执行器；Python 入口为 `scripts/main.py:run`，接收 inputs 与 ctx，返回 SkillResult。prepare/postprocess 可以处理 prompt 前后材料。

本阶段每个 Work Unit 提交一个主 Artifact，多个产物会被拒绝。SkillResult 包含 ArtifactDraft、NodeProposal、FactProposal、RelationDraft 与质量信息。校验包括输入/输出 Schema、节点类型、产物类型、目标引用、事实类型白名单、节点/关系范围与几何、人工锁定回调及自定义校验器。预算约束允许的模型任务、调用数、token 和墙钟时间。

Artifact、依赖、提案与 RunRecord 原子提交；失败只记录 failed Run，不生成半成品 Artifact。提案存入 skill_proposals，P4/P5 再实现领域投影，当前不会自动成为正式分子或事实。PostgreSQL 提交使用事务 advisory lock 和唯一索引保证 Work Unit 版本递增且只有一个 active。提交后才发送完成事件。

input_fingerprint 覆盖输入、目标材料与图片、节点哈希、上游版本、Skill/提示哈希和模型配置（排除密钥）。缓存命中复用 Artifact 并记录 cached Run；force 新增版本。调用方应提供真实的 upstream_versions，不能省略依赖版本后声称具备自动失效。

## 披露与调用

L0 短描述上限 40 tokens；L1 卡片上限 350 tokens；L2 是已加载工具 Schema；L3 通过 read_skill_resource 按需读取参考小节（最多 4000 tokens）。token 数为本地估算值，实际计费以模型 Usage 为准。

DisclosureContext 字段：user_id、audience、failed_checks、image_types、intents、loaded、token_budget、toolkit。预选先过滤可见性；失败码精确匹配，评分为失败码 100、图像类型 50、意图 10；前 3 项优先 L1，超预算降级或省略。

常驻元工具为 find_capabilities、describe_capability、load_capability、unload_capability、read_skill_resource（共 5 个），加文档核心工具。load 下一轮才生效，同轮调用未加载工具会拒绝；最多加载 6 项，按 LRU 淘汰，4 轮未使用自动卸载。状态存入 AgentState 可随 checkpoint 恢复。capability_disclosures 每次模型调用记录披露、token 估算与使用情况。

Tool 与 Skill 通过统一索引发现；内部能力不能直接加载给聊天 Agent。工具领域名允许点号，OpenAI 传输层将不合法名称映射到稳定别名，历史与返回调用名称同步转换，应用仍使用原名。

## 模型与路由

`ModelClient.chat(request, task="default")` 与 TaskRouter 冻结。任务名通过 models.yaml 解析；当前文本问答使用配置的 GPT-6 Luna、Responses、中等思考，视觉任务使用独立 VLM 配置。不得在 Skill 内写死端点或密钥。

routing.yaml 的 schema_version=1，routes 支持 subtype、any、run、when；`{subtype}_extract` 模板与 `a+b` 合并结构化调用有效。加载时检查引用和 D38：每条链至多一次视觉结构化调用。planned 列表仅表示 P4/P5 后续能力，不能执行或当作完成产物。

## 首批能力与扩展方式

visual_classify、paper_qualify 默认 active；figure_describe 默认关闭；markush_assemble 是调用 markush.substitute 的 Python Skill，演示代码与 Tool 组合。基础 Tools：markush.substitute、esmiles.parse、esmiles.infer_role、mol.backfill、molparser.health、molparser.recognize。

复杂图像应逐步组合检测、识别、标注、一次视觉结构化、校验、回填和确定性代码。当前已有注入、白名单、执行、版本与路由基础；逐类图像抽取、检测 GPU 服务和正式分子/事实投影是 P4/P5 的开发任务。旧流水线取舍见 `docs/references/legacy-pipeline/README.md`。

## HTTP

前缀 `/api/v1`：GET /skills、/skills/catalog、/skills/{name}/versions、/skills/{name}/card、/skills/{name}/resources。POST /skills:reload 需要配置非空 SKILLS_ADMIN_TOKEN 且提供匹配的 X-Admin-Token；未配置时拒绝。只读 API 的完整用户鉴权仍属 P7。
