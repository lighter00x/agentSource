# P4：分子链路

> 周期：D2～D7 ｜ 负责人：BE-2（主）
> 前置：P2 的节点与坐标换算、裁剪接口；P3 的 SkillRuntime（可先用桩直接调用函数）
> 设计文档对应：6.5 化学实体、8.2～8.4 检测/识别/绑定、8.7～8.7a SAR 与 Markush 组装、8.11 化合物归一、8.13 分子框编号引用、24 现有资产复用

---

## 1. 目标

把论文图像中的化学结构变成可定位、可校验、可引用的化学事实：

```text
视觉节点 → MolDet 检测 → 分子子节点（带坐标）→ MolParser 识别（E-SMILES）→ RDKit 校验
        → 标注图（红框 + 蓝色 mol_id）→ 按 mol_id 回填 → 化合物归一（编号、别名、结构候选）
```

本阶段大量复用现有流水线 `paper_20260930` 的代码，同时修复设计文档 24.2 节列出的缺陷。

## 2. 范围

**做**

- MolDet 常驻服务（`services/moldet/`）及其客户端。
- MolParser 客户端迁移与改造。
- Skill：`molecule_detect`、`molecule_recognize`、`som_render`、`label_bind`（几何候选）、`mol_backfill`、`markush_assemble`。
- 化学核心 `chem/`：E-SMILES 拆分与解析、RDKit 规范化、InChIKey、分子式、角色推断。
- 拼接器迁移：`chem/splicer/`（保留 `LICENSE.MolParser`）及适配层。
- 化合物模型与基础版 `compound_resolve`（编号、别名、结构候选、采纳优先级；OPSIN 在 P6 接入）。
- 现有测试迁移为回归用例。

**不做**

结构化抽取（表格、路线图由 P5 负责，本阶段用手工构造的抽取结果测试回填）、OPSIN、路径推理。

## 3. 目录与模块

```text
services/moldet/
├── app.py                        # FastAPI：POST /detect、GET /health
├── model.py                      # 常驻加载 moldet_v2_yolo11n_640_general.pt；批量推理
└── Dockerfile
drugpaper/
├── gateway/
│   ├── molparser.py              # 迁移自 recognizer/recognizor.py
│   └── moldet.py
├── chem/
│   ├── esmiles.py                # split、parse（Translator.parse_caption / parse_groups / refactor）
│   ├── normalize.py  formula.py
│   ├── roles.py                  # infer_molecule_type_from_esmiles
│   ├── markush.py                # 适配层
│   └── splicer/esmiles_splicer.py + LICENSE.MolParser
├── facts/
│   ├── compounds.py  labels.py  resolver.py
├── skills/builtin/
│   ├── molecule_detect/  molecule_recognize/  som_render/
│   ├── label_bind/  mol_backfill/  markush_assemble/
tests/unit/chem/                  # 迁移的 splicer、角色推断用例
tests/unit/facts/                 # 回填、归一
```

## 4. 关键设计要点

### 4.1 MolDet 服务

- 启动时加载模型并常驻，支持 GPU 和 CPU；批量推理。
- 参数沿用现有配置：`conf=0.15`、`iou=0.15`、`imgsz=640`、`agnostic_nms=True`。
- 返回 `input_index`、`xyxy_px`、`xywh_norm`、`confidence`、`cls`。坏图对应空结果，不影响其他图的位置。
- 修复现有问题：不丢弃 conf 和 cls；配置不在 import 时固化；标注图不按 basename 保存。

### 4.2 MolParser 客户端

- 每批不超过 32 张；发请求前查 `/health`，队列接近上限时等待。
- 严格按 `input_index` 绑定结果；缺失的单张重试 1 次。
- 网络错误和 5xx 指数退避重试 2 次。
- E-SMILES 按 `esmi → caption → raw_caption` 回退。

### 4.3 molecule_detect 的后处理

- 扩框改为按框自身宽高各外扩 4%，裁剪到父区域内（修正现有"只扩 x、按绝对坐标"的问题）。
- 整图判定改用面积并集。
- 嵌套框置白：小框与大框交集占小框面积 > 0.45 时，在大框的裁剪图中涂白。
- 编号按阅读顺序（先行后列）重排，这就是标注图上的 `mol_id`。
- 与已有 molecule 子节点按 IoU ≥ 0.7 对齐，保持 ID 稳定。
- 跨页合并图按页面 segment 分别检测。

### 4.4 E-SMILES 原样保留

- `e_smiles_raw` 逐字保存，永不参与规范化；`smiles_core` 是 `<sep>` 之前的部分；`canonical_smiles` 只用于比较和计算。
- 存储前后逐字节比对（校验器 V-C02）。

### 4.5 mol_backfill

- 迁移 `Utils/molecule_backfill.py`：按 `(节点, mol_id)` 精确匹配；错误码沿用。
- 补上 `mol_ids` 列表支持；不原地修改，回填前的原始输出作为 Artifact 保存。
- 角色标注沿用 `annotate_image_molecule_types` 的规则。

### 4.6 markush_assemble

适配层处理：prime 编入键名；`H` 映射为 `[H]` 后 `RemoveHs`；预估组合数并拦截超限；返回值统一为列表；错误转为结构化错误码；位置变量跳过并记录。

### 4.7 compound_resolve（基础版）

- 汇总编号来源：几何绑定、手工构造的 SAR 行与路线节点（P5 之后是真实抽取结果）、正文中的编号提及（本阶段用正则）。
- 编号规范化：统一连字符、识别 `12a`、`(S)-26`、`UCM-1306`。
- 结构采纳优先级：人工 > OCSR（分子式一致或置信度高）> Markush 组装 > OCSR 低置信（OPSIN 在 P6 加入）。
- 候选 InChIKey 不一致时生成 Conflict。

## 5. 任务分解

| # | 任务 | 负责 | 估时 |
|---|---|---|---|
| 4.1 | MolDet 服务：模型常驻、批量、返回 conf/cls、坏图位置、健康检查、Dockerfile | BE-2 | 0.75 天 |
| 4.2 | MolDet 与 MolParser 客户端（分批、对齐、重试、背压） | BE-2 | 0.5 天 |
| 4.3 | `molecule_detect` Skill：后处理、分子子节点、坐标换算、ID 对齐 | BE-2 | 0.75 天 |
| 4.4 | `som_render`：标注图，按内容哈希缓存 | BE-2 | 0.25 天 |
| 4.5 | `chem/`：E-SMILES 拆分与解析、RDKit 规范化、分子式、角色推断 | BE-2 | 0.5 天 |
| 4.6 | `molecule_recognize` Skill：StructureCandidate 入库、校验 | BE-2 | 0.5 天 |
| 4.7 | 拼接器迁移、适配层、11 个现有用例迁移 | BE-2 | 0.5 天 |
| 4.8 | `mol_backfill`（含 `mol_ids`）、角色标注、错误报告；现有回填用例迁移 | BE-2 | 0.5 天 |
| 4.9 | `label_bind` 几何候选（框下方和右侧的文本层 span、邻近标签） | BE-2 | 0.25 天 |
| 4.10 | 化合物模型与表结构；基础版 `compound_resolve` | BE-2 | 0.75 天 |
| 4.11 | 分子框叠加检查图：把所有分子子节点的页面坐标画回页面图，用于人工核对坐标换算 | BE-2 | 0.25 天 |

## 6. 测试

| 类别 | 用例 | 期望 |
|---|---|---|
| 迁移用例 | `test_esmiles_splicer.py` 的 11 个用例 | 全部通过，返回值统一为列表 |
| 迁移用例 | `test_molecule.py` 的角色推断 | `""`、`"CCO<sep>"` → unknown；`"*CC<sep>"` → substituent；带 R 组 → scaffold |
| 迁移用例 | `test_molecule_backfill.py` 的 10 类 schema 回填 | 全部通过；新增 `mol_ids` 用例 |
| 迁移用例 | `test_yolo.py` 坏图位置 | 通过（修正 Windows 路径断言） |
| 检测 | 样例论文 Scheme 1、Figure 2、Table 1 | 分子框数量与人工标注一致（误差记录在案）；叠加检查图中框与结构对齐 |
| 坐标 | 分子框局部坐标 → 页面坐标 → 再转回 | 往返误差 < 0.5% |
| ID 稳定 | 同一节点重跑检测 | 已有框保持原 ID |
| 识别 | 化合物 3、26 的结构 | canonical SMILES 分别与 `O=Cc1ccc(-c2ccccc2OCF)cc1`、`FCOc1ccccc1-c1ccc(S(C)(=N)=O)cc1` 一致（用 InChIKey 比较） |
| E-SMILES | 含 `<sep><a>0:R1</a>` 的结果入库再读出 | 逐字节一致 |
| 拼接 | 表格行的母核 + 取代基 | 组装出合法分子；位置变量行记为 `assembly_skipped` |
| 归一 | `26`、`compound 26`、`UCM-1306` | 归为同一化合物，别名齐全 |

## 7. 验收标准

- [x] 迁移用例全部通过。
- [ ] 样例论文所有含结构的视觉节点完成检测、识别、标注图生成；叠加检查图经人工确认坐标正确。
- [x] 化合物 3 和 26 的结构正确入库，E-SMILES 原文保留。
- [x] 用手工构造的表格抽取结果，完成一次 `mol_backfill` + `markush_assemble` + `compound_resolve` 的完整流程。
- [x] 接口冻结：`MoleculeBox`、`StructureCandidate`、`Compound`、`RGroupAssignment`、MolDet 服务接口。

## 8. 风险

| 风险 | 对策 |
|---|---|
| MolDet 在 `conf=0.15` 下误检文字和坐标 | 检测阶段不删，由 P5 的结构化与自检确认；OCSR 解析失败也作为误检信号 |
| MolParser 对低分辨率扫描图识别差 | 按 300 dpi 裁剪；P6 接入 OPSIN 交叉校验 |
| 拼接器在特殊结构上异常 | 全部异常转为结构化错误，不中断流程；结果标注来源 |

## 9. 交给下一阶段的内容

- P5：标注图和 `mol_id` 机制，供所有结构化 Skill 使用；`mol_backfill` 接在结构化之后执行。
- P6：化合物台账、结构候选与冲突，供 OPSIN 接入和路径推理使用。

## 完成记录

2026-10-02：S4.0～S4.12 开发与各项单元测试完成，版本0.4.0。450项单元、6项 PostgreSQL 集成回归通过。真实样例17个视觉节点、54个识别候选，3/26的预期 InChIKey 匹配，17页叠加图已输出。

第二项验收仅保留独立人工签核未完成：检测/识别/标注均已真实运行，Codex 已检查主要结构坐标，未取得每页人工专家签核。样例位图编号缺少文本 span，S4.9几何候选实现与单测通过，原示例“每框正确标签”不能由现有文本层提供；按阶段划分交 P5 完成。图表误框也保留供 P5 自检。

证据：`samples/outputs/phase-4/20261002-final/{report.json,tests.xml,completion.md,overlay/}`。接口和逐项实现/测试映射分别见 `docs/interfaces/chemistry.md`、`docs/codemap/phase-4-codemap.md`。Git 仓库尚未初始化，未打 `p4-done` 标签。

## 2026-10-01 Tool 基础提前实现

按用户要求完成 YOLO 检测服务和 molecule.detect / molecule.detections / molecule.crop / som.render 的注入、共享 Worker、多用户轮转队列与资产隔离，并使用真实权重验证。接口见 `docs/interfaces/molecule-detection-tool.md`。该完成记录仅覆盖 Tool 基础，不能代表 P4 的分子节点投影、正式识别对齐或全部 Skill 已完成。
