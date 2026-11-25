# Tool、Skill 与 Agent 自主调用验收

调用链为：Agent发现能力→读取/加载能力卡→下一轮获得精确参数→公开SkillTool→受归属检查的材料→Skill白名单Tool→Schema与语义校验→持久化产物→后续Skill按产物ID读取。Agent选择图类、调用参数和错误恢复；可靠性由服务端材料、Schema、化学与完成证据约束共同保证。

## 修复的公开调用边界

- 能力发现支持中文短语与多个关键词排序，并优先精确能力名；未匹配提供重试提示和可见工具包，不把空搜索当成未安装能力。
- 加载能力同时返回L1卡片；公开Skill参数包含包的输入Schema。自动读取材料的Skill（例如识别与编号）明确使用inputs={}，不接收复制的detections/化学串。仅有声明输入字段的Skill披露artifact_inputs。
- 识别与编号必须有当前目标的检测产物；视觉Skill检查检测/编号产物、编号图指纹及分段数，提供编号图在先、对应原图在后的材料。缺失/过期材料返回可恢复错误，不消耗VLM调用。
- 后续Skill可使用artifact_inputs={输入字段名:{artifact_id:产物ID,path:路径}}，例如mol_backfill的extraction_payload指视觉产物；scheme_relations的extraction可指回填产物的path=["payload"]。服务端检查用户、文档、目标、活跃状态与路径，禁止重复参数。回填的extraction_payload引用还检查产物类型：识别候选ocsr_result不能替代图类视觉抽取。
- 大结果返回完整产物ID与状态，保留payload_omitted标记；后续服务端读取完整JSON，不要求模型复制。JSON Schema错误返回字段和原因，避免把整个源产物作为错误文本重复给模型。
- 生命周期completed与内容needs_review分开。Python与prompt产物的unclear/errors被标成待复核；原始视觉结果不确定时，成功分子绑定也不能让整体回填成为内容通过。视觉Skill输入中的识别/计算化学字段也被服务端拒绝，防止通过Agent复制结果重新注入VLM。
- RequiredCallsValidator可用于具有明确完成要求的任务，核对实际成功工具消息，拒绝没有调用证据的提前提交；支持某组图类Skill至少调用一个，不规定参数、固定顺序或伪造化学结果。终止预算仍然有效，预算耗尽必须保留未完成状态。
- Agent轨迹独立记录产物ID和内容复核标记，可核对最终持久化结果，不只相信模型的最终说明。

## 验收方法

Tool层覆盖参数错误、超时、并发/用户隔离、检测/识别协议、化学输出序列化和失败不生成产物。Skill层以公开SkillTool走真实注册表、材料、白名单工具、SQLite持久化和九类视觉调用契约；视觉响应受控，用于验证编排，不作为视觉准确率证明。Agent层验证渐进加载、下一轮权限、完成门槛与实际嵌套调用。

真实自主验证使用 scripts/verify_skill_autonomy.py：仅给目标和需求，由现有模型自主调用；从调用轨迹取最终回填产物ID并审查持久化结果。可传--gold独立核对Figure1的数量、标签、位置和连接结构。失败运行分别保存，不修改历史产物来通过验收。

```bash
# 已有隔离论文库，实际模型/YOLO/MolParser；output必须为新目录。
.venv/bin/python -m scripts.verify_skill_autonomy <目标ref> <output> --gold tests/fixtures/jmedchem_2c00949/p5_figure1_molecule_gold.json
```

当前进程若继承SOCKS代理而未安装其依赖，需按运行环境选择可用代理或直接连接；不能把网络初始化失败当作Agent验收通过。该独立核对不认证手性，也不能推广为全部SAR、反应系列、PROTAC和3D图像的化学准确率。

## 真实验收记录（2026-10-04）

真实模型为现有配置的gpt-6-luna（medium）；视觉、检测和MolParser沿用工程配置。目标为隔离论文的p001.b003。最终运行目录：samples/outputs/phase-5/20261002/live-skill-autonomy-typed-handoff-20261004/。

最终report.json的status=passed：实际自主完成检测、识别、编号、structures_extract和mol_backfill；产物ID从工具轨迹取得，两个分子数量、标签、左右位置与连接结构通过独立人工gold。运行中两次错误调用被模型修正，错误保留在trace.json，不掩盖为全程无错误。手性不在独立核验范围。

此前失败运行分别保留：空搜索、误传上游参数、底层Tool与Skill混淆、预算不足、图级/局部识别历史候选歧义、把识别产物错当视觉产物；最终通过不覆盖这些失败。当前候选按分子节点明确的structure_id选择，历史仍保留，不按SMILES相同或时间偷偷合并，也不从失败当前候选回退。

SkillRuntime执行契约升为skill-autonomy-v1，避免旧生命周期/材料语义缓存绕过新规则。普通FigureAgent仍保留质量检查阶段，本次自主性验收针对共用ReAct AgentRuntime与公开Skill调用入口；不宣称所有阶段已替换成自由规划。
