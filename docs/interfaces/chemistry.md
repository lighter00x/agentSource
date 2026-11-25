# P4 化学接口冻结

版本：0.4.0，2026-10-02。类型定义见 `drugpaper/facts/compounds.py`，导出的 JSON Schema 见 `chemistry.schema.json`；Worker HTTP 契约见 `molecule-detection-worker.openapi.json`。

## 调用

```bash
.venv/bin/python -m drugpaper.cli molecules --doc <document_id>
.venv/bin/python -m drugpaper.cli molecules --doc <document_id> --force
.venv/bin/python -m scripts.verify_phase_4
.venv/bin/python -m scripts.debug_molecule_overlay --doc <document_id>
```

检测、逐分子识别、标注、标签候选、归一通过 SkillRuntime 执行，不绕过包声明、校验、预算或原子提交。单分子识别目标为 `{document_id}/r{revision}/{parent_local_id}.mNNN`，Work Unit 为 `molecule_recognize:<ref>`。可独立调用识别 Skill；父节点批量调用也支持 ≤32 张分批。Skill Tool 输入统一为 `target_ref`、`inputs`。

| Skill | inputs | 产物与事实 |
|---|---|---|
| molecule_detect | 可选 preset、min_ratio | molecule_boxes；分子子节点、removed 状态 |
| molecule_recognize | 空对象 | ocsr_result；StructureCandidate、角色与 review 状态 |
| som_render | 空对象 | som_image；按 segment 保存内容寻址 PNG |
| label_bind | 空对象 | label_candidates；几何候选，不自动采纳 |
| mol_backfill | extraction_payload、image_type（1～10） | 原始输出、回填输出、逐字段错误、LabelClaim、角色 |
| markush_assemble | caption+definitions；或 caption+rows[{label,values}] | 统一 products 列表、MarkushScaffold、RGroupAssignment、结构候选与编号声明 |
| compound_resolve | 可选 scope=[compound_id] | 化合物、别名、候选绑定、结构冲突 |

## 几何和 ID

父节点每个 region 按 300 dpi 分别检测。框自身宽高各外扩 4%，裁剪至父区域；whole_image 使用面积并集。小框过滤的 min_ratio=0.015 表示短边相对父图的比例；按整块面积过滤会漏掉大型路线图中的合法结构。嵌套小框与大框交集占小框面积 >0.45 时，在大框裁剪图中置白。

`local_bbox` 和 `parent_region_index` 是权威坐标，页面 regions 由它们推导。标注图编号为 `reading_index`，从 0 开始；Tool 原始 label_id 从 1 开始，不能混用。重跑按 IoU≥0.7 一对一匹配旧 ID；新 ID 使用下一个序号，历史 removed ID 不重新分配给另一结构。消失或移动的框使旧 OCSR 候选 inactive；不删除历史 Artifact。提交时校验文档归属、revision、当前几何和人工锁；并发提交使用文档事务锁，材料过期则失败重跑。

## 识别和 E-SMILES

`MolParserClient.recognize` 支持 bytes、Path、URL、base64，统一 multipart，不按返回列表位置绑定。网络/5xx 重试两次；队列占用>80%等待，有总等待上限；缺失/空的单张独立补救一次。服务异常返回合并推理批次的全局索引时，丢弃批量映射，逐张补救；只有单输入单输出的请求可证明唯一对应，异常原始索引保存为 service_input_index。

E-SMILES 依次取 esmi、caption、raw_caption；`e_smiles_raw` 保留 UTF-8 原文，拆分只按第一个 `<sep>`。canonical_smiles、InChIKey、formula 是派生值。非法结构作为 invalid 候选保存，节点 needs_review；合法 SMILES 本身不意味着它就是正确的论文结构。普通分子角色保持 unknown，schema 确认后才升级 full。

识别和标注缓存包含图像内容、几何、嵌套遮罩、配置和框架版本，排除自身派生 ID。外部权重更新时修改 `MOLPARSER_MODEL_VERSION`，或 force 重跑；未提供服务权重摘要时无法自动发现同一端点背后的模型更换。

## 回填与台账

十类旧 schema handler 已迁移，支持 mol_ids、数值取代基和旧路线拓扑；输出深拷贝，原始抽取保存在 Artifact。错误保留 reason，并补充 code、field_path、available_mol_ids；未引用框记录独立告警。明确的 E-SMILES scaffold/substituent/sru 角色优先于泛化 full。

Markush 支持原始 prime 和编码的 R_prime、H→[H]、RemoveHs、组合上限、逐行单值；位置变量返回 skipped / positional_variable。保留 MolParser 许可证。底层桥连接、无定义、非法母核和无产物转换为结构化错误；原始 E-SMILES 不被改写。

编号归一支持 compound 26、26、UCM-1306、区间与独立立体变体。采纳顺序为 manual > 合法/高置信 OCSR > Markush > 低置信 OCSR；有 HRMS 结果时纳入 formula_match。候选先比较 InChIKey 前14位，再比较完整键，区分 connectivity/stereochemistry 冲突；锁定结构不覆盖。scope 仅更新指定化合物，保留其他冲突。OPSIN 和实验段 HRMS 自动关联按 P6 实现。

迁移 `0006_chemistry` 新建 compounds、compound_aliases、structure_candidates、markush_scaffolds、rgroup_assignments、label_claims、chemistry_conflicts。节点/事实投影与 Artifact、依赖、提议及 Run 在同一事务中提交；失败全部回滚。

## 检测服务和读取

服务保持一个模型、一个专用推理线程和用户轮转队列，取消不提前释放推理槽位。启动预热；GET /health 返回 loaded、device、queue_size、queue_maxsize。POST /detect 保留单图 Tool 协议；POST /detect/batch 为 JSON images 批量；POST /detect/files 为 multipart files 批量。返回 input_index、width/height、xyxy_px、xywh_norm、confidence、cls，坏图留空槽。认证支持 Bearer 与 X-Service-Token，参数由 YOLO_* 配置固定，避免并发请求改写全局配置。Dockerfile 从工程根目录构建，有 CPU/GPU target；当前运行环境采用已验收的 systemd GPU Worker。

只读 API：GET /api/v1/documents/{id}/compounds、GET /api/v1/documents/{id}/compounds/{compound_id}。`list_compounds` Tool 仅访问上下文所属用户/文档，支持别名查询，返回已采纳结构和节点证据 refs。HTTP API 身份仍沿用 default；完整鉴权在 P7。

## 真实验收边界

样例论文17个视觉节点完成检测、标注、标签候选；54个候选完成逐分子识别并可经 RDKit 校验，3/26 的预期 InChIKey 匹配，17页叠加图已生成。Scheme 1 / Figure 2 / Table 1 分别11/5/1框。Codex 视觉检查确认主要结构框和页面坐标对齐；图表页仍有一个检测误框，保留未绑定候选供 P5 自检。

图内编号是位图，没有原始文本 span；本样例 label_bind 返回空列表。几何候选功能已用可用文本跨度的单元用例验证，图内编号的识别和最终绑定由 P5 的结构化 Skill 完成。3/26 的验收绑定来自已知样例 InChIKey 核对，不能视为自动编号理解。54个合法候选也不能视为54个全部正确的分子。
