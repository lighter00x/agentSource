# P1 / P2 真实验收

阶段完成记录中的离线通过，不等于真实依赖已联调通过。真实验收必须验证模型的工具调用能力、真实 PDF 的 MinerU 提交链路、PostgreSQL 的原生检索与恢复，以及答案和证据的正确性。

## 运行

在仓库根目录运行：

```bash
uv sync
uv run python scripts/verify_live_p1_p2.py --direct
```

配置读取根目录 `.env` 和 `config/models.yaml`。必须配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`MINERU_URL` 和 PostgreSQL `DATABASE_URL`。模型任务默认沿 `tasks.default → stages.default → pools.llm` 解析。

`--direct` 仅对本次验收和它的子进程禁用 HTTP / HTTPS / SOCKS 代理，适用于内网服务；不修改 `.env` 或 shell 的持久配置。需要代理时去掉此参数。

脚本使用已配置 PostgreSQL 实例新建随机命名的 `dp_accept_*` 验收数据库。连接用户需具有创建数据库和安装扩展的权限。迁移升降只在该新库进行，**不对 `.env` 中的业务库执行迁移或清空**。新库保留用于人工复核；数据库名写入报告，连接密码不写入报告。若新库创建失败，PostgreSQL 检查记为 FAIL，同时使用仓库内 SQLite 临时库继续诊断其他链路。

默认 PDF 是 `tests/fixtures/jmedchem_2c00949/*_origin.pdf`。`--pdf /absolute/path/paper.pdf` 可以换成其他 PDF，但当前图表节点与问答预期针对默认样例，不能将其直接用于其他论文的质量评分。其他论文应另建对应人工预期。

报告和问答结果保存在 `samples/outputs/acceptance/<时间>/`，包含 `report.md`、`report.json`、检查日志、真实问答 JSON、裁剪图、在线管线结果、恢复后的结果。Blob 和 JSONL 追踪保存在 `data/acceptance/<时间>/`。时间使用 Asia/Shanghai。

## 判定依据

| 项目 | 必须验证的行为 |
|---|---|
| 代码质量 | ruff、format、mypy、单元测试通过；单元测试与真实服务配置隔离 |
| P1 模型调用 | 普通对话可以返回内容；模型可以自动选择并调用文档工具 |
| P1 问答 | UCM-1306 对应 26；研究对象是 D1 受体；5 步内结束且带有效引用 |
| P1 预算与追踪 | max_steps=1 能按预算退出或一步完成；JSONL 有起止与模型调用记录 |
| P2 PostgreSQL | 独立库迁移 up/down/up、IR 快照往返、FTS、原生 pg_trgm 检索通过 |
| P2 恢复 | 第一个独立 Python 进程写入检查点后注入中断；第二个进程恢复并仅补做剩余调用 |
| P2 IR 问答 | Table 3 标题为 ADMET Profile for Compound 26；化合物 26 在含摘要的全文首次出现定位到摘要 p000.b007，在不含摘要的正文首次出现定位到引言 p001.b002；引用有页面区域 |
| P2 API | 文档树、节点、页面图、裁剪图、检索都返回正确结果 |
| P2 在线解析 | 真实 PDF 提交到 MINERU_URL，经提交、轮询、获取产物、IR 入库和页面渲染达到 ready |
| P2 在线结果 | 基于新解析 revision 再做真实问答和 API 检查，而非继续查询离线 revision |

完整验收要求报告全部 PASS。FAIL 必须修复后重跑；SKIP 不能作为通过。`--skip-quality` 仅供服务联调诊断，报告会标记 SKIP 并返回非零退出码。恢复测试使用 FakeLLM 注入可重复中断，验证的是持久化和恢复；真实问答能力由其他 live 检查验证。

现有阶段脚本仍可单独运行：

```bash
uv run python scripts/verify_phase_1.py --smoke
uv run python scripts/verify_phase_2.py --smoke
```

注意：原 P2 脚本即使带 `--smoke`，解析输入仍是离线 fixture，且其演示库是临时 SQLite；它不能替代上述在线 MinerU 与真实 PostgreSQL 验收。P1 的 `--smoke` 已收紧为模型配置错误时直接失败，不再静默回退 FakeLLM。

## 已发现的模型服务要求

2026-10-01 的真实调用中，普通对话成功，但 Agent 请求收到 HTTP 400：

```text
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
```

这说明连接参数已生效，但推理服务未开放本项目使用的 `tool_choice="auto"` 能力。若后端为 vLLM，需要在服务启动配置中启用 `--enable-auto-tool-choice`，并设置与实际模型、模板和 vLLM 版本匹配的 `--tool-call-parser`。只在客户端 YAML 中声明 `tool_calling: true` 不会开启服务端能力。

依据：[vLLM 官方工具调用文档](https://docs.vllm.ai/en/stable/features/tool_calling/)。不要仅凭服务对外声明的模型名称猜测 parser；需结合实际部署配置确认。服务修复后用相同验收命令复测。

## 尚需人工验收

2026-10-01 后续配置：Agent 问答已改用 `https://ai.102465.xyz/v1` 的 `gpt-6-luna`，中等思考。`config/models.yaml` 的文本模型设置 `api: responses`、`extra_body.reasoning.effort: medium`；视觉模型保持独立配置。密钥只在本地 `.env`。GPT-6 Luna 在非 none 思考级别下使用 Responses 工具调用，依据 [OpenAI 模型文档](https://developers.openai.com/api/docs/models/gpt-6-luna)。早期报告中的 Qwen 工具调用错误属于原文本端点配置，不能代表切换后的问答结果。

默认样例只能证明这一篇论文的结果。P2 的其他排版论文续段抽查仍需补齐至少两篇：检查章节、图注、被合并正文、跨栏 / 跨页续段、归一化坐标和裁剪范围，并保存人工核对记录。接口中的 page 为从 0 开始的索引，阅读器显示页码为 page + 1。

该脚本覆盖后端业务链路和通过 TestClient 调用的 API；不包含 Web UI、外部反向代理、生产部署压测、P3 Skill 运行时或 P4 之后的化学事实抽取。P2 的 ready 只表示文档底座完成。
