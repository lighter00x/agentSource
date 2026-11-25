# drugpaper

药物论文理解 Agent。已实现 P1–P5 工程基础和 P6 全文开发链路；真实文献内容验收进度单独记录，开发计划见 `docs/dev-plan/`。

```bash
uv sync                                   # 安装依赖（含 dev 组）
cp .env.example .env                      # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL；DATABASE_URL 留空则用本地 SQLite

uv run drugpaper db upgrade                                        # 数据库迁移
uv run drugpaper ingest --offline tests/fixtures/jmedchem_2c00949  # 离线导入样例论文，输出 document_id
uv run drugpaper ask --doc <document_id> "文中 Table 3 的标题是什么？"
uv run drugpaper molecules --doc <document_id>                       # P4：检测/逐分子识别/标注/归一
uv run drugpaper serve                                             # API：http://127.0.0.1:8000/docs（无鉴权，仅限本机/内网）

make test        # 单元测试
make test-int    # PostgreSQL 集成测试（TEST_DATABASE_URL 或嵌入式 pgserver）
make verify-p3   # 阶段验收（离线）
```

代码结构见 `docs/codemap/`（`phase-1-codemap.md`、`phase-2-codemap.md`、`phase-3-codemap.md`），冻结接口见 `docs/interfaces/`，变更记录见 `CHANGELOG.md`。

配置真实服务后的 P1/P2 验收：`uv run python scripts/verify_live_p1_p2.py --direct`。
该命令创建独立 PostgreSQL 验收库，实际提交 PDF 到 MinerU 并验证真实问答；操作边界和判定标准见 `docs/dev-plan/real-acceptance-p1-p2.md`。

P3 真实视觉分类验收：`uv run python scripts/verify_phase_3.py --live --direct`。基础 Tool/Skill 注入、Python 组合与旧流水线取舍见 `docs/interfaces/skills-and-capabilities.md`；分类基线及测试证据见 `samples/outputs/phase-3/20261001-183351/completion.md`。

YOLO 分子检测 Tool 已提供本地与共享 Worker 两种方式；多用户、多应用进程建议使用 remote，配置与调用见 `docs/interfaces/molecule-detection-tool.md`。

本机常驻环境已部署：API `http://127.0.0.1:18000/docs`，YOLO 共享 Worker 使用 GPU 0。启动、日志、重建和验收说明见 `deploy/README.md`。

P4 真实验收：`uv run python -m scripts.verify_phase_4`，先运行 P4 单元测试，再用隔离数据库调用真实 GPU Worker 和 MolParser。逐项完成与接口边界见 `docs/codemap/phase-4-codemap.md`、`docs/interfaces/chemistry.md`；样例位图内的编号绑定由 P5 接续。

P5：`uv run drugpaper figures --doc <document_id>` 执行图像理解；`uv run python -m scripts.verify_phase_5` 对已授权的测试文献运行隔离真实验收。批次API `/api/v1/batches` 使用Celery队列，Worker部署见 `deploy/README.md`。每图固定分类/结构化/自检，补救动作按检查码逐步披露；待复核结果和未覆盖字段保留供后续问答与人工审阅。代码地图：`docs/codemap/phase-5-codemap.md`；接口：`docs/interfaces/figure-agents.md`；测试与真实验收：`samples/outputs/phase-5/20261002/completion.md`。


P6：`uv run drugpaper text-facts --doc <document_id>` 准备正文、名称、台账、测定和页包；`uv run drugpaper understand --doc <document_id> --export-dir <目录>` 执行顺序阅读、回查、综合/路径校验、固定 JSON 与 ZIP 导出。支持 `--from-page k` 重读；缺失 P5/T1 材料返回 pending。

`make verify-p6` 回放真实样例正文并运行受控全文链路；`make verify-p6-chem` 验证受控名称。附加一次真实摘要：`uv run python -m scripts.verify_phase_6 --live`。这些开发验证不替代真实文献内容验收。

OPSIN 通过 `OPSIN_BASE_URL` 显式配置，独立服务部署件见 `services/opsin/README.md`。银标准比较：`uv run python -m scripts.evaluate_silver --predictions <独立预测目录> --output <报告目录>`，缺失预测退出2。接口、预算、证据、恢复、API与导出见 `docs/interfaces/full-text.md`，逐项交付和验收限制见 `samples/outputs/phase-6/20261005-full-text/completion.md`。当前P5整体验收、样例26名称/HRMS和十篇独立预测仍未通过，未标p6-done。

P8 前期开发调试工作台已提供于 `web/`：文档区域与节点检查、Agent Run 时间线/详情、阅读检查点、请求日志与受控调试操作。启动见 `web/README.md`，实际范围与后续计划见 `docs/dev-plan/execution/phase-08-early-workbench.md`。本轮未重启现有业务服务，新增监控接口需由开发后端加载。

2026-11-16 前端接续：已扩展化合物/本地结构图、测定条件、图表、全文证据、阅读回查和检索等视图。运行 `cd web && npm run dev:offline` 可启动独立离线联调后端与工作台，默认前端5173/API18001，数据保存在专用 `data/workbench-dev/`，无需先恢复 MinerU/MolParser。详见 `web/README.md`。
