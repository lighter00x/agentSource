# P3：Skill 运行时与渐进式披露

> 周期：D2～D5 ｜ 负责人：BE-4（主）、BE-3（首批 prompt 型 Skill）
> 前置：P1 扩展点冻结；P2 的 `artifacts`、`runs` 表与节点 API 可用（可用桩先开工）
> 设计文档对应：6.10～6.11 过程模型与 Work Unit、8.15 Prompt 体系、9.1～9.6 Tool 与 Skill 协议、能力披露

---

## 1. 目标

建立"能力"的完整管理体系，让后续所有功能都以 Skill 的形式接入：

1. **Skill 能加载、能执行、能留痕**：目录包格式、校验、热加载、执行、提交 Artifact 和依赖。
2. **Agent 能按需发现和使用能力**：渐进式披露（L0～L3）、代码预选、元工具、动态激活的工具集。
3. **模型调用按任务名发起**：为 P7 的模型网关预留接口。

## 2. 范围

**做**

- Skill 包格式（`SKILL.md` + `manifest.yaml` + schemas + prompts + tests）、加载器、校验器。
- Skill 注册表（`skill_versions` 表），作用域 `builtin` 和 `system`（`user` 在 P7 接入用户后启用）。
- 热加载：文件监听 + Redis 频道通知 Worker。
- SkillRuntime：`prompt` 型和进程内 `python` 型；`SkillResult` 的原子提交；按 `input_fingerprint` 缓存。
- Work Unit 与 Artifact 版本：同一 Work Unit 同时只有一个 active 版本。
- 分层 prompt：`prompts/common/*` + Skill 专用段；PromptAssembler 按预算装配。
- 能力索引、CapabilityRouter、5 个元工具、会话级动态工具集。
- `routing.yaml`：图像类型 → Skill 链（P5 使用）。
- `ModelClient.chat(task=...)`：任务名映射到模型（本阶段用一张简单映射表，P7 换成五层注册表）。
- 首批 Skill：`visual_classify`、`paper_qualify`、`figure_describe`（可选）。

**不做**

沙箱执行（P9）、用户私有 Skill（P7 启用作用域）、graph 型 Skill。

## 3. 目录与模块

```text
drugpaper/
├── skills/
│   ├── protocol.py               # SkillManifest、SkillResult、ArtifactDraft、NodeProposal、FactProposal
│   ├── loader.py                 # 解析目录包、校验、计算 content_hash
│   ├── registry.py               # 版本、状态机、作用域解析 user→system→builtin
│   ├── runtime.py                # 执行、提交、缓存
│   ├── watcher.py                # 热加载
│   └── builtin/
│       ├── visual_classify/
│       ├── paper_qualify/
│       └── figure_describe/
├── capabilities/
│   ├── index.py                  # 能力索引（L0/L1 文本、triggers、token 计数、向量可选）
│   ├── router.py                 # CapabilityRouter
│   ├── assembler.py              # PromptAssembler
│   ├── meta_tools.py             # find / describe / load / unload / read_skill_resource
│   └── toolset.py                # DisclosureToolset：替换 P1 的 StaticToolset
├── domain/artifacts.py  runs.py  work_units.py
prompts/
├── common/                       # data_boundary、mol_reference、numeric_transcription、no_hallucination
└── skills/<name>/task.md
config/routing.yaml
```

## 4. 关键设计要点

### 4.1 Skill 生命周期

`discovered → validating → testing → staged → active`，与设计文档 9.3 节一致。本阶段的 `testing` 只执行 `prompt` 型和进程内 `python` 型 Skill 的用例。

校验内容：

- manifest 字段完整，`accepts`、`produces`、`tools`、`model_tasks` 引用存在；
- 输入输出 Schema 可解析；
- prompt 模板可编译，引用的公共片段存在；
- `disclosure.budgets`：L0 ≤ 40 tokens、L1 ≤ 350 tokens，超出拒绝激活；
- `SKILL.md` 的 L1 固定小节齐全（缺失时给出警告）。

### 4.2 SkillRuntime 的提交流程

按设计文档 9.5 节，在一个事务中完成：

1. 输出 Schema 校验；
2. 权限校验：提议的事实类型在 `produces` 中，子节点只能挂在目标节点下；
3. 几何校验；
4. 锁定保护（本阶段先实现接口，锁的来源在 P8）；
5. 执行 manifest 声明的校验器；
6. 写入 Artifact 新版本（旧版本标记 `superseded`）、依赖边、RunRecord；
7. 发布事件 `artifact.updated`。

缓存：`input_fingerprint = sha256(输入内容 + 上游 Artifact 版本 + Skill 版本哈希 + prompt 哈希 + 模型名 + 参数)`，命中即直接返回已有 Artifact。

### 4.3 渐进式披露

完整实现设计文档 9.6 节中不依赖用户和沙箱的部分：

| 组件 | 本阶段内容 |
|---|---|
| 能力索引 | 从 `SKILL.md`（L0/L1）、manifest（triggers、audiences、toolkit）、Tool 的短描述生成；按注册表版本缓存 |
| CapabilityRouter | 可见性过滤 → 触发匹配（check_codes、image_types、intents 关键词）→ 排序 → 按预算装配；向量相似度检索作为可选项，默认关闭 |
| 元工具 | `find_capabilities`、`describe_capability`、`load_capability` / `unload_capability`、`read_skill_resource` |
| DisclosureToolset | 实现 P1 的 `ToolsetProvider`：每轮返回"常驻 + 已激活"的工具；最多激活 6 个；最久未用的先卸载；连续 4 轮不用自动卸载 |
| PromptAssembler | 实现 P1 的 `ContextAssembler`：系统规则 → 任务材料 → 能力描述（≤ 预算）→ 历史 → 输出预留；超预算按 L1→L0→删除低分候选的顺序降级 |
| 披露记录 | 每次调用写 `capability_disclosures`：披露了什么、多少 token、用了什么 |

### 4.4 首批 Skill

| Skill | 类型 | 说明 |
|---|---|---|
| `visual_classify` | prompt | 10 + 3 类（设计文档 7.5 节），修正现有 `classify.md` 的多路线矛盾；输出 evidence 和 confidence；附图注；使用任务 `visual_classify` |
| `paper_qualify` | prompt | 首页准入判断（设计文档 7.2a） |
| `figure_describe` | prompt | 图像文字描述，默认关闭，用于验证"新增 Skill 无需改代码" |

## 5. 任务分解

| # | 任务 | 负责 | 估时 |
|---|---|---|---|
| 3.1 | Skill 协议模型、manifest Schema（含 `disclosure`、`model_tasks`） | BE-4 | 0.25 天 |
| 3.2 | 加载器与校验器；`content_hash` | BE-4 | 0.5 天 |
| 3.3 | 注册表与状态机；`skill_versions` 表；作用域解析 | BE-4 | 0.5 天 |
| 3.4 | 热加载（watchfiles + Redis pub/sub）；进行中的 Run 使用启动时版本 | BE-4 | 0.25 天 |
| 3.5 | SkillRuntime：prompt 型执行、进程内 python 型执行、提交事务、缓存 | BE-4 | 0.75 天 |
| 3.6 | Work Unit key、Artifact 版本、依赖边写入 | BE-4 | 0.25 天 |
| 3.7 | 分层 prompt 目录；公共片段（从 prompt_v2 抽取）；PromptAssembler | BE-3 | 0.5 天 |
| 3.8 | 能力索引与 CapabilityRouter | BE-4 | 0.5 天 |
| 3.9 | 元工具与 DisclosureToolset；接入 AgentRuntime | BE-4 | 0.5 天 |
| 3.10 | `ModelClient.chat(task=...)` 与任务映射表 | BE-4 | 0.25 天 |
| 3.11 | `visual_classify`、`paper_qualify`、`figure_describe` 三个 Skill 与用例 | BE-3 | 0.75 天 |
| 3.12 | `routing.yaml` 格式与解析（P5 使用） | BE-3 | 0.25 天 |
| 3.13 | Skill 与能力的只读 API：`GET /skills`、`/skills/catalog`、`/skills/{name}/card` | BE-1 | 0.25 天 |

## 6. 测试

| 类别 | 用例 | 期望 |
|---|---|---|
| 加载 | manifest 缺字段；Schema 非法；L0 超预算 | 状态为 `invalid`，原因可查 |
| 热加载 | 往 `skills/` 放入新的 prompt 型 Skill | 不重启，几秒内出现在注册表，可执行 |
| 热加载 | 修改正在执行的 Skill | 进行中的 Run 使用旧版本，新 Run 使用新版本 |
| 执行 | 对样例论文所有视觉节点跑 `visual_classify` | 每个节点一个 Artifact，重复执行命中缓存 |
| 提交 | 输出不符合 Schema；提议越权的事实类型 | 拒绝提交，写入失败原因 |
| 预选 | 模拟 `failed_checks=[C_AXIS_RESIDUAL]` | 候选只包含声明了该触发码的能力，总 token 不超预算 |
| 动态工具集 | FakeLLM：`find_capabilities` → `load_capability` → 调用该工具 | 激活后下一轮才出现在工具列表中；调用成功 |
| 卸载 | 连续 4 轮未使用 | 自动卸载；超过 6 个时按最久未用卸载 |
| 装配 | 能力描述超预算 | 按 L1→L0→删除低分候选降级，任务材料不被挤掉 |
| 权限 | 调用未披露的能力 | 拒绝 |

## 7. 验收标准

- [x] 样例论文所有视觉节点完成分类，分类结果与参考视觉判断的一致率记录在案（作为基线）。
- [x] 新增一个 prompt 型 Skill 只需要放入目录，不改代码、不重启。
- [x] P1 的问答 Agent 切换到 DisclosureToolset 后，只常驻 5 个元工具加核心工具，能通过发现和激活完成需要额外能力的问题。
- [x] `capability_disclosures` 中能看到每次调用披露的能力及 token 数。
- [x] 接口冻结：`SkillManifest`、`SkillResult`、`DisclosureContext`、`routing.yaml` 格式、`ModelClient.chat(task=...)`。

## 8. 风险

| 风险 | 对策 |
|---|---|
| 能力描述写得不清楚，模型选错 | 用披露命中率和选错率反馈（设计文档 9.6.8）；L1 卡片模板化 |
| 热加载导致 Worker 间版本不一致 | 以注册表版本号为准；每个 Run 记录完整版本标识 |
| 缓存键遗漏影响因素，返回过期结果 | 缓存键包含全部输入哈希；提供 `force=true` 绕过 |

## 9. 交给下一阶段的内容

- P4、P5：所有分子和图像能力都以 Skill 形式实现，走 SkillRuntime 提交。
- P5：FigureAgent 的补救决策使用 CapabilityRouter 预选动作。
- P6：DocLookup 规划使用预选的检索工具。
- P7：`ModelClient.chat(task=...)` 由网关接管；`user` 作用域启用。
- P8：对话 Agent 直接使用 DisclosureToolset。

## 完成记录

（阶段结束时填写）

## 2026-10-01 实施与验收状态

S3.0～S3.14 开发项已完成：协议、加载/注册、热更新、执行与原子提交、缓存、分层提示、模型任务、能力索引/预选/动态工具、首批 Skill、路由、API 与接口冻结。按用户补充要求增加旧流水线 Markush/角色/回填基础 Tool、MolParser Tool 和 Python 组合 Skill；复杂图像链保留 P4/P5 planned。

验收记录见 `samples/outputs/phase-3/20261001-183351/completion.md`。26 个视觉节点完成真实分类且缓存复用通过，参考一致率 21/26（80.8%）；参考由 Codex 视觉复核，用户/领域专家签核待补。动态工具常驻元工具实际为 5 个（包含 read_skill_resource）。仓库未初始化 Git，因此 p3-done 标签待团队创建，不作为代码已经打标的声明。
