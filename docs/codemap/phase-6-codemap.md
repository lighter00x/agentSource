# P6 开发代码地图

日期：2026-10-05。开发交付已接入；真实内容验收保持 in_progress，接口0.6.1已冻结。

| 功能 | 代码 | 验证 |
|---|---|---|
| 正文模型与证据、名称标题 | drugpaper/facts/text_models.py | tests/unit/reader/test_text_extraction.py |
| 编号区间、别名、V-E02、长段切分、唯一引文定位 | drugpaper/facts/text_extraction.py | 同上 |
| Skill 包、模型任务、公开调用 | drugpaper/skills/builtin/text_extract/、config/models.yaml | 包内回放及公开 SkillTool 测试 |
| 归属、跨页、分类、依赖就绪、token 单元 | drugpaper/agents/reader/page_pack.py | tests/unit/reader/test_page_pack.py |
| 持久化管线与命令 | drugpaper/orchestration/text_pipeline.py、drugpaper/cli.py:text_facts | reader 回归、test_cli.py |
| 独立样例起步验收 | scripts/verify_phase_6.py | samples/outputs/phase-6/20261005-foundation-final/ |
| OPSIN 平台服务、只读 Tool | drugpaper/chem/opsin.py、drugpaper/tools/builtin/opsin.py、drugpaper/services.py | tests/unit/chem/test_opsin.py |
| 实验标题、公式/加合离子、候选投影 | drugpaper/facts/name_extraction.py、drugpaper/chem/formula.py、name_to_structure Skill | tests/unit/reader/test_name_ledger.py |
| 正文台账、优先级/冲突、角色初判 | drugpaper/facts/text_ledger.py、drugpaper/facts/resolver.py、compound_resolve Skill | 同上、tests/unit/chem/test_resolver.py |
| 原文/章节/revision、锁与历史、引用保护 | drugpaper/facts/projector.py、name_sources.py、reference_delivery.py、skills/bootstrap.py、runtime.py | 同上（原子回滚、换版、过期引用） |
| 化学受控/真实服务验证 | scripts/verify_phase_6_chemistry.py、make verify-p6-chem | samples/outputs/phase-6/20261005-chemistry/ |
| 独立 JVM OPSIN 服务/容器交付件 | services/opsin/、docker-compose.yml | opsin-service.json、opsin-ledger-final/report.json（真实本地服务） |
| 当前源快照、证据与组件指纹 | drugpaper/facts/full_text.py | tests/unit/reader/test_full_text.py（归属/编辑/缓存失效） |
| 正文测定/条件正式映射 | drugpaper/facts/text_measurements.py | 同上（幂等/过期/条件/冲突） |
| DocLookup 批量/快路径/白名单/缓存 | drugpaper/agents/lookup/{batch,fast_path,tools,cache,models}.py | 同上（6题2模型调用、重复/非法动作）；scripts/verify_phase_6_lookup.py（真实样例10题） |
| VisionLookup 按页合并/1600px | drugpaper/agents/lookup/vision.py | 同上（页隔离、零工具） |
| NoteDelta、问题队列与保留证据压缩 | drugpaper/agents/reader/{notes,queue}.py | 同上（自然解决、8000token、指针保留） |
| 顺序阅读/每页checkpoint/重读 | drugpaper/agents/reader/graph.py、prompts/skills/reader_page/ | 单测及 tests/integration/test_full_text.py（独立Services/PG恢复） |
| 综合/一批确认/12000token档案 | drugpaper/agents/reader/synthesize.py、prompts/skills/reader_synthesize/ | 路径综合回放/调用预算测试 |
| MCS差异/可执行替换/变化分类 | drugpaper/chem/{diff,change_classifier}.py | 九类分子对/重建InChIKey测试 |
| 环名/路径V-P01～08/虚拟中间体 | drugpaper/chem/ring_lexicon.py、config/ring_lexicon.yaml、facts/paths.py、validators/path.py | V-P反例/多区域sanitize测试 |
| LangGraph全文主图/最多两次回灌 | drugpaper/orchestration/full_text_pipeline.py、pipeline_graph.py | 校验修正、三次上限及完整链路测试 |
| 用户固定JSON/Profile/审计投影 | drugpaper/profiles/、validators/projection.py | E-SMILES逐字、条件选择、V-J01～05/V-E01测试 |
| ZIP/Markdown/CSV/chunks/manifest | drugpaper/exporters/ | API/哈希/图像紧邻描述/曲线/800token测试 |
| 四张P6表与迁移 | persistence/models.py、persistence/migrations/versions/0008_full_text.py | SQLite及PG迁移升降/模型一致性测试 |
| API/CLI/Celery入口 | api/full_text.py、cli.py、workers/tasks.py | HTTP全文/重读/导出/归属测试 |
| 银标准兼容/缺失预测显式报告 | drugpaper/evals/silver.py、scripts/evaluate_silver.py、tests/fixtures/silver/ | 旧字段/无效SMILES/缺失预测测试，真实十份参考manifest |
| 全流程验证与接口冻结 | scripts/verify_phase_6{,_full_text}.py、freeze_phase_6.py | verify-final/、docs/interfaces/phase-6/manifest.json |

接口说明：docs/interfaces/full-text.md；逐项S6.0～S6.12状态见开发执行方案末尾。最终证据目录：samples/outputs/phase-6/20261005-full-text/。877项单元/集成测试通过；真实P5、样例26/HRMS和十篇独立预测尚未通过，因此不使用p6-done标记。正文泛化准确率与整篇内容不能由受控回放认证。
