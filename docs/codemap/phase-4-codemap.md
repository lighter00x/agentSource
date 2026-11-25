# P4 代码地图

版本：0.4.0，2026-10-02。阶段开发项 S4.0～S4.12 已实现，每项均有单元测试；真实样例与后续阶段边界见 `docs/interfaces/chemistry.md`。

| 任务 | 实现 | 单元测试 |
|---|---|---|
| S4.0 化学环境/迁移回归 | chem/splicer、facts/legacy_backfill、config/services.yaml | chem/legacy/test_splicer.py、test_backfill.py、chem/test_core.py |
| S4.1 共享检测服务 | detection/api、backend、service；services/moldet | detection/test_backend.py、test_service.py、chem/test_worker.py |
| S4.2 客户端 | gateway/moldet.py、molparser.py；Tools | chem/test_clients.py、test_molparser.py、detection/test_remote.py |
| S4.3 分子检测 Skill | skills/molecule.py:detect、vision/boxes.py、facts/projector.py | chem/test_boxes.py、test_pipeline_skills.py |
| S4.4 编号标注 | skills/molecule.py:som、vision/som.py | chem/test_boxes.py、test_pipeline_skills.py（缓存/跨页） |
| S4.5 化学核心 | chem/esmiles、normalize、formula、roles | chem/test_core.py |
| S4.6 识别及入库 | skills/molecule.py:recognize、0006_chemistry、projector | chem/test_pipeline_skills.py（原文/invalid/局部重跑/回滚） |
| S4.7 Markush | chem/splicer/adapter、markush、skills/molecule.py:markush_assemble | chem/legacy/test_splicer.py、test_core.py、test_legacy_tools.py |
| S4.8 回填 | facts/backfill、legacy_backfill、skills/molecule.py:mol_backfill | chem/legacy/test_backfill.py（十类 schema）、test_pipeline_skills.py（mol_ids/整链） |
| S4.9 标签几何 | facts/label_geometry、bootstrap.py 原文 span 材料、label_bind Skill | chem/test_boxes.py、test_pipeline_skills.py |
| S4.10 台账及归一 | facts/compounds、labels、resolver、projector；api/compounds；list_compounds Tool | chem/test_resolver.py、test_pipeline_skills.py（别名/采纳/引用/隔离） |
| S4.11 页面叠加 | scripts/debug_molecule_overlay.py | chem/test_pipeline_skills.py（页面像素对照） |
| S4.12 验收/冻结 | scripts/verify_phase_4.py、interfaces/chemistry* | chem 全量单元；integration/test_postgres.py（迁移/原子投影） |

公共链路：CLI molecules → MoleculePipeline → SkillServices.context_for → SkillRuntime → 白名单 Tool / Python → Schema 与权限校验 → 文档事务锁 → Artifact + 节点/事实原子提交。每个分子独立 Work Unit，历史候选保留 inactive。

新增七个事实表，迁移编号使用0006，避免与 P3 的0005冲突。已有基础 Tool、GPU 多用户队列、Blob 用户命名空间继续复用。新增六个 Python Skill 包与升级的 markush_assemble，每包有三项注册回放，复杂分支由独立单元测试覆盖。路由中移除已实现 P4 的 planned 标记，保留 P5 图像结构化能力。

真实证据：`samples/outputs/phase-4/20261002-final/report.json`、`completion.md`、`overlay/`。开发验收不等同于全分子正确率或专家签核；位图标签抽取、图表误框确认、OPSIN 和人工纠错 UI 分别由 P5/P6/P8 接续。
