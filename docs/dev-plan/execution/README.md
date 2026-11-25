# 分阶段执行方案：使用说明

`docs/dev-plan/phase-*.md` 回答"每个阶段做什么"；本目录回答"**按什么顺序、具体怎么做、做到什么程度算完成**"。每个阶段一份执行方案，拆到可以直接照着写代码的粒度。

---

## 1. 步骤格式

每个阶段由若干**步骤**组成，编号 `S<阶段>.<序号>`，例如 `S1.3`。每个步骤固定包含以下内容：

| 项 | 说明 |
|---|---|
| 负责 / 时长 | 角色（BE-1～BE-4、FE-1、FE-2）和预计时长（按 0.25 天为单位） |
| 前置 | 必须先完成的步骤 |
| 产出 | 新增或修改的文件，精确到路径 |
| 执行 | 具体要写的类、函数、配置、SQL，以及关键逻辑 |
| 验证 | 可以直接运行的命令和期望结果 |
| 完成标志 | 满足即可勾选；未满足不能进入依赖它的步骤 |

## 2. 执行节奏

- **每个步骤一个分支**：`feat/S1.3-openai-client`。完成标志满足后发起合并请求，由另一位成员评审后合入阶段集成分支 `phase/1-agent-loop`。
- **每日检查点**：每个阶段的执行方案都列出了每天中午和傍晚应当完成的步骤。站会只看检查点：完成、延后（原因）、阻塞（需要谁）。
- **阶段验证脚本**：每个阶段提供 `scripts/verify_phase_N.py`，一次性跑完该阶段的全部验收项并输出报告。阶段验收以脚本结果为准。
- **接口冻结**：阶段最后一步统一把接口写入 `docs/interfaces/`。冻结后修改接口需要在合并请求中说明影响范围，并通知依赖方。

## 3. 通用命令约定

```bash
make up            # 启动 docker-compose（Postgres + pgvector、Redis）
make down
make lint          # ruff + mypy
make test          # 单元测试（不访问任何外部服务）
make test-int      # 集成测试（需要 make up）
make smoke         # 真实模型端点冒烟测试（需要 .env 中的端点配置）
make verify-pN     # 运行 scripts/verify_phase_N.py
```

## 4. 测试数据

- 样例论文 `10_1021_acs_jmedchem_2c00949` 的全部 MinerU 产物复制到 `tests/fixtures/jmedchem_2c00949/`（layout.json、middle.json、content_list.json、images/、merged_images/、origin.pdf）。
- 每个阶段再补充 3～5 篇排版不同的论文到 `tests/fixtures/extra/`，由 BE-1 在 P2 开始前准备。
- 现有流水线 `paper_20260930` 的产物作为银标准，放在 `tests/fixtures/silver/`（P6 起使用）。

## 5. 文件列表

| 文件 | 阶段 |
|---|---|
| `phase-01-execution.md` | P1 最小 Agent Loop |
| `phase-02-execution.md` | P2 文档底座 |
| `phase-03-execution.md` | P3 Skill 运行时与渐进式披露 |
| `phase-04-execution.md` | P4 分子链路 |
| `phase-05-execution.md` | P5 图像理解多 Agent |
| `phase-06-execution.md` | P6 全文理解与产出 |
| `phase-07-execution.md` | P7 模型管理与用户 |
| `phase-08-execution.md` | P8 交互、对话与重跑 |
| `phase-09-execution.md` | P9 沙箱、加固与发布 |
