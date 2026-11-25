# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按开发阶段记录。

## [Unreleased] - 2026-11-25 · 点位策略与按需结构审查

- 结构化模板新增端点级 `position_policy` 与实例级 `position_status`：区分 required/optional/not_applicable/unknown/exhausted 与 provided/not_printed/unreadable/missing/not_applicable；`position_definitions` 仍是可选的来源事实，不强制每个成员都有点位。
- 审查 host 检查按策略放行：只有 `required` 配 `missing`/`unreadable` 阻塞；`not_printed`、`not_applicable`、`exhausted` 不阻塞；`unknown` 先取证并以 warning 提示；声明 `not_applicable` 但 substituents/markush/position_definitions 中存在位置前缀时报错。
- `scheme_extract` 0.19.0、`structure_audit` 0.6.0；`common_extract`、`table_extract`、`structures_extract` 等同步可选字段及补丁版本。
- 结构审查改为按需触发：首次提取后审查一次；局部补丁、来源登记、试拼不再自动复审；准备回填前主动调用 `review_structure` 一次性复审，避免每改一次就审查。
- 审查未通过时先交付 ID-only 流程图（`flowchart_only`），分子回填标 `pending_review`，不再整单阻断。
- 新增只读工具 `inspect_attachment_sites`：结构化阶段披露识别连接点标签并逐实例校验 attachments 完整性，不披露识别 SMILES/分子式。
- 定向单元测试通过；新快照的真实模型运行与验收另记，历史结果不自动改写。

## [Unreleased] - 2026-11-21 · 产物Markush组合与结构拼接

- mol_backfill 0.5.0接入产物专用的组合枚举与显式连接点拼接，反应物保持通式；等价赋值合并，完整候选保存SMILES/分子式/来源，未编号候选不生成论文编号声明。
- 原子占位、环位CH/N、H/卤素及支持的基团逐点处理；未知Alk、非法连接点、组合超限保留局部失败，不编造结构。
- 母核E-SMILES校验保留标注，修正记录保存理由，结构修改仅使回填及后续失效。
- 本图保留原识别并按图核对两处母核连接点后，仅从post重跑：1–10图示通式生成6个候选，第二通式40组中32组成功、8组Alk未定义；报告与独立结果已保存。

## [Unreleased] - 2026-11-21 · 最终结果JSON排版

- 最终结果文件和下载接口统一为UTF-8、两格缩进的多行JSON对象；修正工作台及导出副本的单行紧凑格式，数据内容不变。

## [Unreleased] - 2026-11-21 · 新结构化结果后续全流程比较

- 复用gpt-6-luna结构化结果，完成回填、确定性检查、视觉复核与报告，保存独立最终JSON和比较记录。
- 四步路径、两组系列和Markush内容保留；5个检测框绑定、回填错误0，历史旧报告有2项回填错误。
- 最终needs_review：系列成员结构未解；实体追溯检查仅看图注导致可见图内编号被警告。派生relations缺少无编号起始hit的一条边，但完整pathways保留4步；本轮比较保留这些限制，未改写检查结论。

## [Unreleased] - 2026-11-21 · gpt-6-luna单图结构化实测

- 使用用户授权的Figure 2验证新API：首轮暴露变量定义类型、通式ID和产物未展开问题；scheme_extract 0.9.1补强角色约束并增加产物展开示例。
- 复用上游后，结构化提取一次请求68.6秒完成，无格式修复；四箭头、10/15编号集合、反应物不展开及产物6/14项局部候选通过核对。
- 保留unclear及未解成员状态；按测试范围暂停在回填前。真实模型输出与核对记录保存至samples/outputs/figure2-gpt-6-luna-*.json，未替换正式最终报告。

## [Unreleased] - 2026-11-21 · 结构化提取切换gpt-6-luna

- figure_structure独立路由至已有LLM_BASE_URL/LLM_API_KEY，使用gpt-6-luna和Responses API，保留图像输入及JSON Schema。
- 保留240/480/600秒递增请求时限和5/10秒指数退避；分类及复核路由保持各自配置。

## [Unreleased] - 2026-11-21 · 反应物Markush只保留通式

- scheme_extract 0.9.0对reactants中的Markush不再展开取代组合，保留通式、变量选项、编号系列与图中依据；products沿用局部取代展开。
- 新增not_expanded状态，区别主动不展开与识别不清；更新提示词内示例及Figure 2预期结构化文件，历史实际运行结果不改写。

## [Unreleased] - 2026-11-21 · 路线解析任务完善

- scheme_extract 0.8.1将完整中文执行规则与通过Schema/语义校验的JSON示例集中到task.md，移除usage.md及其引用；补充起点/终点、逐箭头拓扑、分支与汇聚、实际上下文范围、条件代号溯源、ID/论文编号对应及返回前验收，保留Markush候选与具体实例的区分。
- 明确pathways.steps为规范路线输出，补充旧optimization_steps/extra字段迁移说明；重复编号范围端点也必须完整填写compound_series。
- 非跨箭头链接错误不再被通用隔离异常覆盖，保留原始字段及语义校验错误供调试。

## [Unreleased] - 2026-11-21 · Markush缺失字段与格式修复提示

- 定位单图重跑失败：模型及格式修复结果均遗漏两处markush.evidence_text；保留历史失败与原始响应，不补造依据。
- scheme_extract 0.7.1明确每个Markush对象自身的图中依据为必填；Schema错误展开为具体字段路径及缺失属性，供模型修复和开发调试使用。

## [Unreleased] - 2026-11-21 · 结构化提取等待时限

- figure_structure 请求超时逐次为240、480、600秒；视觉服务最多3次尝试，失败后按5、10秒指数退避加0–1秒抖动（上限60秒）。重试等待事件写入调试记录。
- 对应9个提取Skill总时限为2700秒，覆盖常规重试与一次Schema修复；达到总时限仍终止执行。
- 传输重试/时限变化不再使已完成的分类等上游结果失效；模型或输出参数变化仍按原规则要求重跑。新建运行采用新配置，历史运行快照保留原配置。

## [Unreleased] - 2026-11-21 · JSON代码查看器

- 通用JSON展示改为CodeMirror只读代码查看器，提供完整格式化源码、行号、语法配色、括号匹配、代码折叠、全文查找、自动换行及复制，替换逐节点树形视图。
- 保留单图结果文件下载和凭据屏蔽；长数组/长值不截断，轮询内容相同时保留滚动与查找状态；编辑器资源由项目本地打包。
- 构建、前端单元测试、14项浏览器回归通过；现有Figure 1局域网页面验证行号、高亮、只读与Ctrl+F，无页面错误。

## [Unreleased] - 2026-11-21 · 单图结果文件与JSON树形查看

- 单图回填与报告完成时保存独立可下载JSON，带来源、回填内容和检查状态；支持历史结果补存，每次运行保留独立版本，未回填时不导出伪最终结果。
- 调试页与图像报告加入结果文件入口；通用JSON预览替换为树形渲染器，支持折叠、筛选、分页、长值、源码和复制。

## [Unreleased] - 2026-11-21 · 流程图端点内Markush通式展开

- scheme_extract 0.7.0在反应物/产物各自的markush中解释通式、逐变量候选及局部取代方案，支持等价赋值合并、原文/符号替换依据、覆盖状态和未知说明。
- 可见基团方案与论文编号实例分开：允许局部取代组合展开，禁止将候选方案直接分配给编号范围；同名变量在不同端点不串用。回填完整保留解释，不对其进行实例组装或生成确定分子式。

## [Unreleased] - 2026-11-21 · 单图路线中的化合物系列

- scheme_extract 0.6.0 区分编号集合、通用骨架与具体化合物；单图缺少逐成员定义时保留编号、变量选项及未知状态，不依赖未提供的Table，不按取代基组合生成结构。
- 新增compound_series输出契约及语义校验，禁止把1–10当成一个实例、把选项列表当具体赋值或将未解系列标为ok；保留独立绘出的具体化合物。撤销历史资源中枚举所有组合的冲突指令。
- scheme_relations 0.6.0区分reaction/optimization/selection/unresolved；不将未解系列或优化箭头投影成具体合成反应。通用母核不触发具体化合物查询，不展示确定分子式，不产生逐成员结构事实。历史结果不自动改写。
- 154项本地模拟/运行时/持久化回归通过，Ruff与mypy通过；局域网服务已重载。未重新执行真实模型解析，验证记录见samples/outputs/frontend/20261121/。

## [Unreleased] - 2026-11-20 · 全过程开发调试基础版本

- 文本块点击后支持 OCR 文本与公式混排，读取对应 revision 的 MinerU 原始公式片段；保留原文/LaTeX 源码及失败提示，无需重新解析已有文献，证据字符偏移保持不变。

- 用户明确授权后完成单张TOC样例真实分类、检测识别及暂停/继承重跑验证；第二次运行不重复检测识别，结构化提取完成。仅此一图，不代表整篇或内容准确率验收通过。

- 在既有前后端接入全流程及分阶段工作区：合并布局/OCR、自动分类路由、单图阶段执行、暂停继续、继承有效上游的新运行及历史并排查看。
- 新增 workbench API、单机持久化控制记录与独立源码快照进程；全包/共享工具局部编辑、校验、试运行和主动全局更新，局部版本不激活到全局注册表。模型重试、请求/响应、工具与中间图像逐步可查。
- 分子详情显示分子式/SMILES及渲染或截图，支持PDF绘框、调整/删除、人工SMILES和恢复原始结果；独立检测/全部或单分子识别，单分子重识别后仍保留人工有效值。
- 上传支持manual/auto；隔离开发库可显式连接现有外部服务，单图失败隔离、仅重试问题图。基础版本不替代通用P8、分布式队列或真实内容准确率验收；验证材料见 samples/outputs/frontend/20261120/。

## [Unreleased] - 2026-11-20 · 全过程开发调试需求与设计文档

- 更新总体设计 13.7/17.6、P8 阶段计划/执行方案及前期交接、web README，记录已确认的 MinerU 合并布局、自动/手动解析、单图自动分类路由、逐步查看与暂停、有效上游复用及历史比较需求。
- 补充全 Skill 包与公共工具代码的图表局部编辑/试运行/主动全局发布、分子框与 SMILES 人工修正和恢复、点击分子右侧详情、跨页整体及单图失败隔离；暂不纳入单元格/提取字段/分子关联编辑和父子图交互。
- 同步 IR、图表 Agent、Skill、监控接口文档的待开发边界；新增验收清单均未勾选。本次仅文档变更，不修改运行代码、冻结 Schema 或历史验收结论。

## [Unreleased] - 2026-11-17 · 文本模型端点与主备切换

- 文本模型切换至用户指定的新端点，主模型gpt-6-luna、备用gpt-5.6-terra；私有凭据只保存在.env，视觉端点与凭据保持独立。
- 文本阶段显式启用重试耗尽回退，每模型最多3次请求；超时/连接/429/5xx可切换，鉴权/普通请求/Schema错误保持显式失败。文本provider直连，避免进程继承的SOCKS代理初始化错误。
- CLI问答/恢复统一使用任务路由；候选模型配置纳入Skill/图像报告缓存指纹，实际成功模型由响应记录。
- 61项模型/路由/CLI/Skill回归通过，Ruff/格式/mypy通过。新端点模型列表鉴权通过，真实主模型及受控主失败后的真实备用模型调用通过；报告见samples/outputs/services/20261117/text-model-switch/。

## [Unreleased] - 2026-11-16 · 前端现有接口集成与离线联调

- 扩展P8前期工作台：化合物/结构来源/冲突、测定与实验条件、图像报告/表格/数值曲线、全文字段与审计、逐页阅读/回查、论文检索、能力版本和已有批次状态，均接入当前API。
- 证据核对文档与revision后跳转原页/节点/原文高亮；Unicode码点正确转换；表格归一化bbox联动原图。图像Skill与全文结果可定位到Run。
- 本地RDKit/WASM绘制已有采纳结构，资产随构建提供；领域JSON导出保留完整长度并脱敏。改进请求错误分类、跨资源取消与旧数据隔离。
- 新增一键 `npm run dev:offline` 及独立开发后端入口，使用专用SQLite/文件目录加载已有离线论文；不依赖MinerU/MolParser，不自动调用模型或修改业务环境。
- TypeScript/构建、前端工具测试和浏览器回归通过；实际HTTP离线论文联调零页面错误/零写请求。证据见 `samples/outputs/frontend/20261116/`。仍不替代P5/P6实文验收或完整P7/P8交付。

## [Unreleased] - 2026-11-15 · P8 前期开发调试工作台

- 新增本地 React/TypeScript/Vite 工作台，接入已有文档、树、页面图、节点、图像报告、全文结果及导出接口；提供归一化区域叠加、Inspector、分页与缩放。
- 新增只读 Run 列表/详情、Reader active 检查点 API；按 default 用户/文档归属与删除状态守卫，筛选分页和常见凭据脱敏。前端展示持久化运行时间线、状态、错误、model/metrics/输入输出、父 Run 与 Trace 查询。
- 默认3秒可控轮询、后台暂停、断线旧快照提示；最近100条请求详情、复制 URL 和脱敏导出。调试写操作默认关闭，预览实际请求后确认，支持 Reader、DocLookup、全文参数及导出；不自动重试 POST。
- 验证：前端构建/类型检查、调试工具测试通过，4项模拟 API 浏览器测试通过；9项现有文档与监控 API 回归通过；Ruff及监控模块mypy通过。独立SQLite离线论文实际HTTP浏览器联调通过（真实pending Run、页面图、节点详情、零页面错误），未调用真实模型。
- 限制：P7鉴权、Chat SSE、Correction/rerun预览与完整原P8 UI仍待后端和后续开发；running不是存活证明，历史Run不认证当前revision；本轮未重启或迁移业务服务，不改变P5/P6真实内容验收结论。证据 `samples/outputs/frontend/20261115/`，方案 `docs/dev-plan/execution/phase-08-early-workbench.md`。

## [Unreleased] - 2026-10-05 · P6 全文链路逐项开发交付

根据 docs 的阶段计划、执行方案及 P1–P5 开发/真实测试记录，补齐第六阶段开发交付件。以下为当前状态；早期起步条目保留历史。真实内容验收仍未全部通过，P6 保持 in_progress。

- S6.0：沿用正文 text_extract/V-E02/长段与编号别名基础，完整正文管线补齐测定映射；真实样例60节点回放通过，仍不认证全文模型召回。
- S6.1：新增可部署的独立 FastAPI/JPype OPSIN 2.8.0 JVM 服务、固定 jar SHA256、Compose 可选 profile与运行说明；主Worker仅用HTTP。真实乙醇/N-methylaniline、失败名称及名称Skill→HRMS组成→候选采纳链路通过。客户端隔离环境代理，修复缺失SOCKS依赖的初始化错误。临时服务已停止，未替换业务服务。
- S6.2：DocLookup ≤8题、归一化去重、快路径、共用一次规划/一次作答、只读白名单/CapabilityRouter、20工具/60秒预算、SQL缓存与Run；VisionLookup按页合并1600px图像，零工具。缓存与结果纳入用户/revision/事实/应用及Skill源码/提示词/Profile/配置指纹。真实样例十题图注/别名定位10/10，零模型调用，重复八题全命中；不作为泛化准确率。
- S6.3：完成正文Measurement/Assay正式事务投影、编号别名唯一关联、字典/SI值、原始值/条件/物种/证据保留和幂等；继承OPSIN/OCSR采纳优先级、冲突、人工锁与角色初判。冲突/歧义/过期材料保持复核或排除。
- S6.4：页包增加章节路径，保留7000token单元、T1/图表摘要、脚注、read/skim/skip、pending与needs_review语义。P5失败材料不会被当作可阅读的成功产物。
- S6.5：实现无工具逐页Reader、NoteDelta来源校验、问题队列/自然解答/批量回查、8000token代码压缩与证据指针、每页SQL版本和并发前驱保护、断点恢复及从第k页重读。原始delta和历史版本保留；证据本身超限明确失败，尽力项LLM压缩未启用。
- S6.6：实现一次确认回查、12000token化合物档案综合与PathProposal；输出实体ID、lead/final、实际主路线、策略/SAR/标签/总结和证据。角色记录在综合结果中，不覆盖人工台账。
- S6.7：实现完整环MCS/10秒超时、原子索引/上下文差异、九类变化与中英文环名词典、V-P01～V-P08；多区域尽力生成sanitize有效的虚拟中间体和split_steps审计候选，原步骤保留needs_review。
- S6.8：LangGraph接入阅读→综合→步骤校验→投影；错误反馈最多两次重试，总计三次综合，每次路径/反馈保存在Run。错误声明修正与次数上限已测试。
- S6.9：固定旧用户JSON/Profile（先导化合物字段、数字steps、对象end、字符串null）、原始E-SMILES、最终产物指标/同条件比较、同形叶子审计；V-J01～V-J05/V-E01。无证据叙述置null并复核，冲突或不足指标不编造。
- S6.10：实现私有ZIP、交付/审计JSON、Corpus/审计Markdown、IR/化合物JSON、测定长表CSV、≤800token文本chunks、原图/已有曲线CSV和叠加PNG、文件及组件哈希manifest；新增全文/回查/阅读/重读/导出/下载API、understand CLI、主文档管线开关与Celery任务。
- S6.11：收录10份真实历史Gemini完整JSON银标准及来源SHA；实现旧字段兼容、lead/final InChIKey、标签/指标/步骤比较与缺失预测非零退出报告。当前独立预测覆盖0/10，报告明确不完整，不冒充十篇评测通过。
- S6.12：新增0008迁移（lookups/reader_notes/full_text_results/exports），SQLite与PostgreSQL升降/恢复测试；完整开发验证脚本与11模型/Profile/导出manifest/OpenAPI的0.6.1冻结及SHA清单。接口、README、代码地图和逐项执行记录同步更新。
- 验证：全量877项单元/集成通过（284.43秒，零失败/错误/跳过），增加24项全文专项；随后加强全组件指纹保护，并修复导出查询迭代器被提前耗尽导致manifest调用统计为空的问题，增加Reader及整链调用数断言，24项SQLite/PG全文专项最终复测通过（12.24秒）。Ruff、557文件格式、mypy278源文件通过。受控完整链路V-P/V-J/V-E通过，lead=1/final=26、3模型/0工具、12个导出文件及哈希通过。首次全量旧迁移清单2项失败和十题基准误用原文而非既有规范化文本的失败报告均保留，修正后通过。
- 验收边界：真实P5整体未通过；样例26的OCR名称/设计S位点名失败、真实HRMS缺失；真实Reader人工核验、10篇独立预测与调用数中位数未完成。未标p6-done，目录未初始化Git；未迁移业务库、重启常驻服务或发布。报告见 `samples/outputs/phase-6/20261005-full-text/completion.md`，逐项表见 `docs/dev-plan/execution/phase-06-execution.md`。

## [Unreleased] - 2026-10-05 · P6 实验名称解析与正文台账

- 复盘 docs 中 P1–P5 开发/测试与 P6 起步记录后，接续 S6.1/S6.3 化学代码链路；P5 十篇真实整体验收仍未通过，保留失败/缺失依赖和待复核边界。
- 新增可选 OPSIN JSON HTTP 客户端、平台配置、chem.name_to_structure Tool 和公开 name_to_structure Skill。零 LLM 调用，实验标题与同段 HRMS 生成原文证据；结构候选/标签同 Artifact 原子入库，原始 SMILES/E-SMILES 保留。WARNING、失败、缺失公式或公式不一致显式复核；故障可重试、空重跑撤销旧 active 事实。
- 增加元素组成与明确加合离子比对，记录中性式/离子式解释；未支持的盐、同位素和复杂加合物保持未验证，不将组成匹配称为精确质量或独立化学认证。
- compound_resolve 升级 0.6.0，合并正文提及/别名/OPSIN，补齐优先级、公式/别名冲突、角色初判证据；保留人工锁、稳定编号、立体父子关系和局部 scope。修正过期显式结构 ID 被同节点其他候选替代的问题，增加原文/章节/revision/结构/台账提交前校验与换版证据清理；查询阻断过期名称结构，引用回填同时阻断未验证名称结构。
- text-facts 接入名称与台账，返回 name_runs、compound_resolution 和 p6_complete=false；新增 make verify-p6-chem、受控/真实服务脚本及开发 Schema，更新接口、代码地图和阶段完成记录。
- 真实公开样例4条实验名称全部处理，3条OPSIN候选成功，26的OCR名称含·被拒绝；fixture未发现HRMS，候选仍待复核。设计文档26名称另因S位点失败，原始报告保留。真实乙醇成功链路与受控26回放通过，不能替代样例26/SI化学内容验收。样例60节点正文回放、26/UCM-1306证据台账与页包检查通过，不认证正文模型召回。
- 验证：最终853项单元/集成测试全部通过（288.36秒，0失败/0错误/0跳过），含新增34项回归；Ruff、514文件格式和mypy 247源文件通过。首次测试数据偏移失败与首次全量注册表缺Tool失败均保留，修复后最终通过。默认沙箱aiosqlite阻塞经最小复现确认，数据库测试改用沙箱外隔离测试环境。
- 证据：samples/outputs/phase-6/20261005-chemistry/completion.md 和 tests-all.xml。P6保持in_progress；正式OPSIN部署、样例26/HRMS验收、正文测定映射、DocLookup、ReaderAgent、路径、Profile/导出与十篇银标准继续待办。本轮未迁移业务库、重启服务或发布。

## [Unreleased] - 2026-10-05 · P1–P5 交付加固与 P6 起步

- 根据 docs 阶段计划、完成记录和真实验收报告复查 P1–P5；保留 P5 十篇真实整体验收未通过的结论，不把待复核或产物存在作为内容正确证明。
- SkillQuality 的 failed/needs_review 传递到 Artifact，失败质量禁止事实/节点/关系提案及事务写入；提交后的事件通知异常仅记录日志，不把已提交结果误报失败。升级执行契约缓存指纹，避免复用旧语义产物。
- 自主视觉入口拒绝失败的检测/编号材料，并拒绝过期 revision；合法 needs_review 材料保留可补救路径。正文 Skill 不要求 PDF 图像材料。
- P6 已启动 S6.0/S6.4：新增公开 text_extract Skill 与模型任务、四类正文候选、编号区间/UCM 别名、原文证据与 V-E02、长段切块、实验名称提取、Artifact/Run 原子入库；唯一逐字引文可校正模型偏移，仍显式计数并标 needs_review。正文独立缓存不包含自身旧产物；原文指纹阻断编辑后过期抽取。
- 新增 PagePack：跨页段落归起始页，页分类、正文事实/图表摘要/脚注、7000-token 有界单元、缺失或失败依赖阻断、待复核状态与未核验字段保留。新增 CLI text-facts、make verify-p6、起步 Schema、接口说明和代码地图。
- 真实端点已通过发现/加载/实际 Tool 调用与作答；在 P5 隔离验收库强制重跑 Figure1，两分子的数量、标签、左右位置和连接结构匹配既有人工基准，五种 PNG 均可解码。立体化学和全部图类/文献准确率仍未认证。
- 样例正文 60 节点完成回放入库与页包构建；真实摘要调用单独保存，不把回放空响应或一次模型成功当作全文抽取召回验收。OPSIN、DocLookup、完整归一、ReaderAgent/checkpoint、路径、Profile/导出、十篇银标准和 P6 整阶段冻结仍待完成。
- 验证：开发前基线 785 项单元测试通过，独立 10 项 PostgreSQL/Celery 集成测试通过；最终 819 项单元/集成回归全部通过（248.30 秒，0失败/0错误/0跳过），见本次 completion.md 和 tests-all.xml。Ruff、503 文件格式与 mypy 241 源文件检查通过。SOCKS 代理依赖导致的首次真实冒烟失败、中途回归失败和修正后证据分别保留；单次清除代理变量后真实工具冒烟通过，未修改全局代理配置。
- 证据：samples/outputs/phase-6/20261005-foundation-final/；接口：docs/interfaces/full-text.md；图像报告：该目录 p5-figure1-recheck/report.json。P6 处于 in_progress，未标 p6-done；本轮没有重启或发布正式业务服务。

## [Unreleased] - 2026-10-04 · Tool / Skill / Agent 自主调用验收

- 修复真实模型发现能力的整句精确匹配问题，支持中文短语/多关键词排序和未匹配提示；加载时返回L1卡片，资源工具兼容包内references/路径。
- SkillTool披露实际输入Schema，自动材料Skill明确空输入，结构化下游使用受归属/目标/状态/类型检查的产物ID交接；大结果保留ID和状态，不让截断破坏后续链路。
- 自主视觉入口准备编号图+原图并拒绝缺失/过期材料；检测前置条件、当前候选指针、识别化学字段不进入视觉输入、生命周期与内容复核分离、参数错误精简、轨迹产物ID和RequiredCallsValidator完成证据校验进入运行代码。
- 九类公开Skill调用与Agent加载/嵌套Tool/恢复专项验收；真实gpt-6-luna自主处理隔离论文p001.b003最终通过两分子数量/标签/位置/连接结构人工基准，失败过程保留，未认证手性或全部图类化学准确率。脚本scripts/verify_skill_autonomy.py，说明docs/interfaces/skill-agent-autonomy.md。

## [Unreleased] - 2026-10-04 · 分子图表 Skill 复用契约

- 重写九类视觉解析与十个支撑 Skill 的实际 L1 能力卡，补齐各包本地 molecular-workflow 资源，包含类型步骤、字段与实例、执行与交付、失败复核及验收清单；九类视觉包提供经当前产物 Schema 验证的合法示例。旧流水线参考标明历史范围。
- 专用视觉包接入 ID-only 实例公共规则与领域约束，表格明确逐行来源与指标关联；修正公共提示词的真实图像顺序、编号可能不连续和 unclear/excluded 清单规则。说明各图类可表达范围，避免曲线不支持的实例字段被静默丢弃、无连接点组件被承诺完整组装。
- 完整单元/集成回归760通过，Ruff/格式检查和mypy通过；新增35项检查，通过实际 describe_capability/read_skill_resource 验证可披露、token/截断边界、Schema/箭头语义和模型提示词组装。复用入口见 docs/interfaces/molecular-skill-reuse.md；这些检查不替代新文献的独立化学内容验收。

## [Unreleased] - 2026-10-03 · P5 内容验收修正

- 更正真实批量状态：10篇已经执行结束，整篇处理完成率0%，P5真实验收未通过；196个图表中48个失败、110个待复核。模型自检、错误兜底与JSON文件存在不能当作内容正确性证明。
- 新增逐图内容审计、Table1逐格人工基准比较及本地人工审阅页面；无基准结果标为未独立核验。表格验收不再仅看行数，失败脚本以非零状态退出。
- 表格转写改为紧凑字符串与稀疏元数据，内部仍校验并生成完整TableGrid；受控真实转写80/80单元格匹配，正式FigureAgent重验两表限定内容通过（Table1逐格80/80与脚注，Table3数值/误差/单位/主体/Kd、脚注与关联及所核对测定条件），不宣称整篇通过。

- 修复真实响应在title字段复制整表导致截断；标题改由图注填入，脚注及稀疏关联显式必填。L2保留元数据与覆盖核对，producer拒绝额外字段；新增指标未映射L1保护。
- 修复LaTeX浓度解析与respectively的4/24小时行关联；验收只忽略数学排版差异，错误浓度与脚注错位仍失败。

## [Unreleased] - 2026-10-03 · 全类型分子回填

- 所有图像路由接入检测/识别/编号和统一回填，图表分支也回填；结构化模型材料移除识别SMILES，所有视觉Skill同次提供原图和编号图。
- 新增mol.deliver纯计算Tool，按显式字段编号回填唯一有效候选并保留原编号/路径/片段；严格阻断未绑定框、歧义、错误编号、无效结构及模型自带SMILES。
- 修复可信Skill内部Tool返回默认截断导致大结果丢失；模型侧长度限制保留。新增回填前/后JSON、逐框覆盖及四类PNG产物，明细按8分子分页。
- 最终635项单元/集成回归通过（124.18秒）；Ruff及mypy通过。已完成结构图2/2、路线图5/5、SAR母核1/1的真实绑定与图像交付。Figure1两个分子的标签/位置/连接关系独立核对通过；未认证立体化学或整篇内容。历史失败保留，独立验收边界见molecular-delivery.md。
- 拦截unclear/失败提取被模型自检误认证及缓存复用；混合单个/多个分子引用分别回填，失败引用保留原位置。化合物标签只关联主体分子，不再投影到取代基片段。保存实际模型编号图，结构化失败仍保留源图证据。

## [0.5.0] - 2026-10-02 · P5 图像多 Agent

### 实现

- 完成图像 FigureAgent 子图、Coordinator 黑板v1/v2、页序分发与并发6、一次定向复核、full/query/review三种模式；主图通过 LangGraph Send 扇出，各图独立状态及检查点。
- 增加表格/路线/结构/图表及领域抽取 Skill，固定流程按类型加载唯一结构化包；补救按检查码渐进披露2～4项动作，选中后加载包与白名单Tool。Python包处理长表切片、测定映射、母核组装与CV，保留原图、证据和不确定状态。
- 实现35项指标词典、数值/单位/次级Kd解析、24条L1四维规则、封闭L2清单、综合回绘与材料裁剪。SAR母核必须由明确编号或角色唯一确认，避免选错Markush片段；原图和标注图在同一次表格请求中提供。
- 补救严格限制两轮/四次逻辑模型调用/180秒，保护人工锁和文档归属。结构化成功后自检失败保留needs_review；初始结构化失败记录failed。报告包含调用分类、耗时、缓存、未核验字段、轨迹及子Panel。
- 新增0007五个图像事实/批次表及原子投影，提供测定/报告查询Tool、HTTP只读接口、批次队列与CLI figures。Celery late-ack/prefetch=1、PostgreSQL检查点、Redis租约全局端点并发；修复并行初始化锁与并发索引死锁。
- 接口快照与SHA256清单位于 docs/interfaces/phase-5；代码测试映射见 docs/codemap/phase-5-codemap.md。API升级0.5.0，业务库备份后迁移0007，新增并启用systemd Celery Worker，沿用GPU0共享检测Worker。

### 验证与验收边界

- 全量594项单元/集成测试通过；Ruff、格式检查及mypy（226个源文件）通过。真实Worker硬终止恢复、Redis跨客户端并发和PG跨连接恢复证据保存在 samples/outputs/phase-5/20261002/tests.xml；最终计数和真实批量结果见同目录completion.md。
- 10篇PDF经用户批准调用MinerU8002解析，全部ready；图像处理在隔离PostgreSQL与独立资产目录验收。真实VLM已正确识别50→500受控错误并建议50；这验证自检能力，不代表整篇全部数值正确。
- Table3主测定、SAR母核/记录、10篇≥90%处理完成率仍以真实report.json和completion.md为准。needs_review是待核验输出，不是正确率证明；不将未完成验收标为完成。Docker仅提供模板，当前运行验收为systemd；HTTP用户认证按P7安排。

## [0.4.0] - 2026-10-02 · P4 分子链路

### 实现

- 完成 S4.0～S4.12：迁移化学核心与旧回填/拼接器回归，新增检测、识别、标注、几何标签、十类 schema 回填、化合物归一 Skill，升级逐行 Markush 组装。沿用 P3 包注册、白名单 Tool、独立 Work Unit、Schema 校验与原子提交。
- 扩展共享 YOLO Worker 的预热、批量 JSON/multipart、服务令牌与健康协议，复用单模型多用户轮转队列；识别客户端支持32张分批、混合输入、严格索引对齐、背压、重试及异常索引的单张补救。
- 分子框外扩、并集/嵌套遮罩、跨页坐标及 IoU 稳定 ID；逐字保留 E-SMILES，RDKit 派生结构与非法候选 review；回填精确错误、母核/取代基角色、H/prime/位置变量处理。
- 新增0006迁移与七个化学事实表，支持编号/别名/立体变体、候选优先级、结构冲突、人工锁、局部重跑和历史候选；文档事务锁与过期几何校验防止并发错写。
- 新增 `drugpaper molecules --doc`、化合物只读 API、`list_compounds` 问答 Tool、页面叠加脚本和真实验收脚本。冻结 JSON Schema 与 OpenAPI，代码/测试映射见 `docs/codemap/phase-4-codemap.md`。

### 验证与部署

- 全量450项单元测试和6项 PostgreSQL 集成测试通过，共456项；包括构造 SAR 的检测→识别→回填→组装→归一整链、原文入库、坏图/缺失位置、缓存/重跑、事务回滚、归属隔离与证据引用。Ruff、格式检查及 mypy（162个源文件）通过。
- 真实 GPU Worker / MolParser 验收通过：17个视觉节点、54个识别候选、106次 Skill 调用、17页叠加图；3/26预期 InChIKey 匹配。独立验收数据库和资产保留在 `samples/outputs/phase-4/20261002-final/`，报告 `report.json`、JUnit `tests.xml`、说明 `completion.md`。
- 已备份业务 PostgreSQL 并升级至0006；API/共享 GPU Worker 正常运行，API版本0.4.0，Worker loaded=true/device=0。Docker CPU/GPU target 已提供，未执行容器构建验收。

### 验收边界与交接

- 样例图内编号属于位图，没有可用原始文本 span，几何标签 Skill 返回空候选；可用文本场景已单测。位图编号识别与最终绑定属于 P5；3/26验收绑定来自已知 InChIKey 核对，不能视为自动标签抽取。
- 54个候选 RDKit 合法不代表54个结构均正确，图表页存在未绑定误框，留给 P5 自检。Codex 已检查主要结构叠加位置，尚无独立人工专家签核；原计划“每页人工确认”保留待签核。
- 小框 min_ratio=.015 采用短边比例；整图面积比例会漏掉大型路线图中的真实分子。保留既有 `/detect`，新增批量端点以兼容 P3 Tool；实际服务索引异常通过独立单张补救而非猜测顺序。外部权重更换需更新 `MOLPARSER_MODEL_VERSION` 或 force 重跑。
- OPSIN/实验 HRMS 关联在 P6，HTTP 用户认证在 P7。项目尚无 Git 仓库，未创建 `p4-done` 标签。

## [Unreleased] - 2026-10-01 · 本机基础设施部署与连接配置

### 新增

- **外部服务配置**：根目录 `.env` 配置 `VLM_BASE_URL=http://10.154.24.54:3000/v1`、`VLM_MODEL=Qwen3.6-27B`；`LLM_*` 与 `VLM_LIGHT_MODEL` 通过 dotenv 变量引用暂时复用同一端点与模型。实际 API Key 仅写入 Git 忽略的 `.env`；`.env.example` 提供无密钥模板。
- **MinerU 参数配置**：`MINERU_URL=http://10.154.24.43:8001`；并发、任务超时、轮询间隔、重试次数、退避及提交/状态/结果请求超时均从 Settings 注入现有异步客户端。沿用附件的上传 → 轮询 → 取回协议，不引入另一套同步客户端。
- **MolParser 接口适配**：新增 `gateway/molparser.py`，按附件协议提供异步 `health`、`recognize_files`（multipart）、`recognize_json`（base64 / URL）；`Services.molparser()` 延迟创建客户端，读取 `MOLPARSER_BASE_URL=http://10.154.24.43:8003`、`MOLPARSER_TIMEOUT_S`、`MOLPARSER_REC_ONLY`。识别结果原样返回，`extract_esmiles` 按 esmi → caption → raw_caption 回退，保留非空 E-SMILES 字符串原文。本次仅接入客户端，不代表 P4 分子 Skill、检测、拼接与事实回填完成。
- **独立部署目录**：`/home/xq/projects/drugpaper-infra/`，包含 `docker-compose.yml`、`services.yaml`、PostgreSQL 配置与初始化 SQL、操作说明、验证脚本和备份脚本。沿用现有 Redis 部署的宿主机目录挂载与 `restart: always` 方式，增加健康检查、资源限制与 Docker 日志轮转；PostgreSQL 和 MinIO 镜像按 digest 固定。
- **PostgreSQL**：新建 `drugpaper-postgres`（PostgreSQL 16），宿主机地址 `127.0.0.1:15432`，数据库及用户均为 `drugpaper`；启用 `vector`（0.8.6）与 `pg_trgm`（1.6），创建 `lg` schema。数据、WAL、轮转日志持久化在部署目录的 `postgres-data/`，逻辑备份保存在 `backups/postgres/`。
- **Redis 复用**：保留现有 `redis` 容器及 `/home/xq/projects/redis-docker/`，地址 `127.0.0.1:16379`；已核实 AOF（`appendfsync everysec`）、RDB 与 `noeviction`。为本工程分配 DB 10（应用）、11（Celery broker）、12（Celery result）、13（cache），不修改原 DB 0～2。
- **MinIO**：新建 `drugpaper-minio`，S3 地址 `http://127.0.0.1:19000`、控制台 `http://127.0.0.1:19001`，创建私有 bucket `drugpaper`；数据持久化在 `minio-data/`。准备 `backups/minio/`、`backups/localfs/` 目录。
- **根目录连接配置**：`.env` 已同步实际部署的数据库、Redis、MinIO 连接值及 `BLOB_ROOT` / `RUNS_DIR` 持久化路径，权限为 `0600`，由现有 `.gitignore` 排除；`.env.example` 提供无密钥模板、端口、Redis DB 分配及后续接入说明。LLM/VLM、MinerU 与 MolParser 端点已按用户提供的信息配置。
- **当前本地资产目录**：`/home/xq/projects/drugpaper-infra/shared/blobs/` 与 `shared/runs/`，供当前 LocalFsBlobStore 和 JSONL 追踪使用。

### 验证

- 外部服务配置与 MolParser HTTP 协议相关单元测试 17 项通过，改动文件的 Ruff 检查通过；实际 `.env` 的所有注册模型任务均解析为所配置的共享端点与模型。模拟传输层验证 MinerU 上传/轮询/取回及 60/30/120 秒请求超时配置。尚未提交真实 MinerU/MolParser 任务或运行真实模型调用；临时测试依赖位于 `/tmp/drugpaper-config-check`，项目环境未修改。
- Compose 配置校验及新容器健康检查通过；数据库扩展与向量查询、TCP 密码认证、MinIO 认证写入/读取/删除均通过。
- PostgreSQL 首份 custom-format 备份已成功恢复到独立临时数据库，验证后删除临时数据库；固定镜像 digest 并重建 PostgreSQL 容器后，数据库扩展仍可访问。
- 验证报告：`/home/xq/projects/drugpaper-infra/validation-report.json`。上述验证针对基础设施初始库，未验证应用业务数据的备份恢复。

### 未完成 / 待办

- 应用 Alembic 迁移尚未在新数据库执行；使用前运行 `uv run drugpaper db upgrade`。
- MinIO S3 BlobStore 适配器、Celery Worker、分布式模型限流尚未接入；当前应用仍使用 LocalFsBlobStore，新增 `S3_*`、Celery 与 cache 环境变量为后续实现预留。
- MinIO 与本地资产尚未设置自动备份，PostgreSQL 备份脚本尚未设置定时执行；需进一步安排异机备份及保留策略。
- 新 PostgreSQL 与 MinIO 端口仅绑定本机回环地址，跨机 Worker 接入需配置私网监听地址并更新客户端连接信息。

### P1/P2 真实验收补充（2026-10-01）

- 新增 `scripts/verify_live_p1_p2.py` 和 `docs/dev-plan/real-acceptance-p1-p2.md`：读取真实配置、创建独立 PostgreSQL 验收库、真实 MinerU 提交、真实模型问答、API 检查、两个独立 Python 进程的 checkpoint 中断与恢复。不对配置的业务库执行迁移或清空，验收库保留供复核。
- 收紧 P1 验收：`--smoke` 配置不可解析时直接失败，不回退 FakeLLM；检查答案中的 26 / D1。P2 增加 Table 3 标题内容检查，API 检查支持指定 revision，在线解析后查询新的 revision。
- **真实结果尚未全部通过**。报告：`samples/outputs/acceptance/20261001-170852/report.md`。294 个单元测试通过；真实 PostgreSQL 迁移升降、IR 快照往返、原生 FTS / pg_trgm、跨进程 checkpoint 恢复通过；离线与在线结果的文档树 / 节点 / 页面图 / 裁剪 / 检索 API 通过。
- 真实 MinerU 完成 17 页样例 PDF 的提交、轮询、产物获取、IR 入库及渲染：约 83.9 秒，260 个节点，revision=2。旧 fixture 为 261 个节点；首页的 `p000.g007` 合并图及其 `.p0` 子节点改为 `p000.b008` 独立图，节点数量差异来自图像分组变化，见同目录 `ir_comparison.json`。
- **真实问答阻塞**：模型普通对话通过，但 Agent 工具调用收到 HTTP 400：`"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`。需修复推理服务的工具调用配置后复测，当前不能标记真实问答通过。
- **当前工作区质量门禁未通过**：P3 部分代码存在 1 个 ruff 错误、10 个文件格式问题、4 个 mypy 错误（包括缺少 `drugpaper.skills.materials` 的类型导入）。未为本次验收改动这些未完成实现。
- 其他排版论文的续段、坐标与裁剪人工抽查仍待补齐。原 P1/P2 完成记录表示当时的离线验收，不替代本次真实验收结论。

### GPT-6 Luna 问答接入与复验（2026-10-01）

- 根据用户提供的调用配置，将 Agent 文本模型切换为 `https://ai.102465.xyz/v1` 的 `gpt-6-luna`，`reasoning.effort=medium`；密钥仅写本地 `.env`。视觉模型仍按独立配置解析。
- `ModelSpec` / `ResolvedModel` 增加 `api` 字段；`OpenAICompatClient` 增加 Responses API 的请求转换、SSE 聚合、工具结果续接、结构化输出及错误处理。`Message` / `ChatResponse` 增加 `response_items`，运行时保存原生工具调用与加密思考上下文供续接和检查点重放；兼容网关 `response.completed.output` 为空时使用 `output_item.done` 的完整结果。
- 新增 Responses 回归测试；298 个单元测试和 3 个真实模型冒烟测试通过。本次涉及的 10 个 Python 文件 ruff / 格式检查通过，网关、配置与运行时相关类型检查通过。全局 P3 质量门禁问题仍待处理。
- 真实 P1 问答、预算退出、追踪通过；真实 PostgreSQL 迁移 / IR / FTS / 原生 pg_trgm / 两进程 checkpoint 恢复通过；真实 MinerU 17 页 PDF 解析约 73.8 秒，260 个节点，revision=2；文档树 / 节点 / 页面图 / 裁剪 / 检索 API 通过。报告：`samples/outputs/acceptance/20261001-173032/report.md`。
- 修正 P2 验收中的语义歧义：不含摘要的正文首次出现化合物 26 在引言 `p001.b002`，含摘要全文首次出现位于 `p000.b007`。分别验收两项；FakeLLM 演示在摘要中检索编号 26，在引言中检索 compound 26，避免把参考文献序号 26 当作化合物。r1 和真实 MinerU 生成的 r2 均通过三项真实问答，证据见同目录 `qa_recheck.md`；保留原始失败报告和修正前复测历史。
- 切换后已解决原文本端点的工具调用阻塞。完整验收仍不能标记全部通过：全局 P3 lint / 格式 / mypy 问题、另外两篇论文人工抽查尚待完成。

### 本机 YOLO 权重接入补充

- `.env` 与 `.env.example` 提供自主配置的 YOLO_MODEL_PATH，当前指向旧项目 moldet_v2_yolo11n_640_general.pt；本地 CPU、检测与并发参数已配置，不修改现有模型/MinerU/数据库连接。
- 项目环境安装 CPU torch 2.14.1+cpu、torchvision 0.29.1+cpu、ultralytics 8.4.171；本机使用 `uv run --no-sync drugpaper serve` 保留该 CPU 构建。可选依赖加载改为动态导入，兼容安装与未安装两种环境。
- 新增权重路径从 dotenv 读取和环境变量覆盖的配置回归测试；真实接入验证读取当前配置，而非调用中传入权重路径。

## [Unreleased] - 2026-10-01 · 应用与 GPU 检测常驻部署

- 完成现有 PostgreSQL 业务库备份和应用迁移至 0005_skill_proposals；复用健康 Redis、MinIO；保留外部模型与解析服务参数。
- 部署 systemd 用户服务：API 127.0.0.1:18000（2 Worker），共享检测 127.0.0.1:8010（1 Worker），已启用自动启动和失败重启。8000 原服务保持不变。
- YOLO 使用独立 .venv-yolo / torch 2.6.0+cu124 / GPU 0（RTX 3090）；权重复制至项目 data/models 并验摘要。应用使用 remote；令牌保存在私有 .env / deploy/runtime.env。
- 验收数据库、Redis、MinIO、MinerU 连通、MolParser、能力目录、真实文本模型、VLM、多用户 GPU 检测与缓存；实际文档 Tool 调用检测到 11 个框，GPU 显存约 449 MiB。
- 部署说明 deploy/README.md；报告 samples/outputs/deployment/20261001-233020/report.md。API 仍绑定本机，完整用户登录鉴权按 P7 后续实现。
- 修复 CLI 新 PostgreSQL 库首次问答死锁：checkpointer 并发索引初始化先于业务事务；新增独立新库首次问答回归。实际 GPT-6 Luna 问答约 13 秒返回“UCM-1306 对应化合物 26”及原文引用，详见报告目录 qa.json。
- 验证：375 项单元测试、4 项 PostgreSQL 集成测试、Ruff、格式检查及 mypy 全部通过；管线失败用例显式关闭 MinerU 配置，隔离真实 .env 服务。最终 API/GPU Worker 均 active、enabled、无重启，实际 GPU 0 显存 426 MiB。

## [0.3.1] - 2026-10-01 · 多用户分子检测 Tool 基础

- 新增独立 YOLO Backend 与检测服务：专用单线程模型复用、有界用户轮转队列、每用户配额、超时/取消保留实际推理槽位、关闭等待、图片限制、用户隔离缓存和权重变化检测。
- 新增 molecule.detect、molecule.detections、molecule.crop、som.render；自动注入能力索引及 Skill Tool 白名单调用。使用文档归属和用户 BlobStore 校验，冻结检测编号，分页读取，分别保存检测/裁剪框，修复旧位置相关扩框及重叠框整图合并问题。
- 提供令牌保护的独立检测 Worker 和 remote 客户端，多 API 进程可共用一个队列；local 仅保证单进程，专用 Worker 应保持单进程。YOLO 为可选依赖，普通应用不安装 torch。
- Services 的 Skill 懒初始化加锁，Skill 图片材料改用当前用户存储；检测 Skill 无后端指纹时禁止提前复用旧 Artifact，Tool 结果按真实权重/参数缓存。
- 374 项全量单元测试、Ruff、format、mypy 全部通过；新增 22 项检测、并发、权限与缓存回归。真实 CPU 权重复验：两图、两用户检测数量 5/31/5/31，模型只加载一次，最大同时推理为 1，缓存通过；另通过两个独立应用进程访问同一临时 Worker 的复验。报告与图片：`samples/outputs/molecule-detection/20261001-202449/`。
- 调用及部署文档：`docs/interfaces/molecule-detection-tool.md`。多用户 Tool 边界已经实现，完整 HTTP 用户认证、正式分子/事实投影仍按后续阶段执行。

## [0.3.0] - 2026-10-01 · P3 Skill 运行时与渐进式披露

### 完成

- S3.0～S3.14：严格包协议与 Schema、闭包哈希和不可变快照、版本/回放/作用域/回滚、文件热加载及 Redis 通知、prompt/Python 共用执行、模型预算、输入指纹缓存、Artifact/依赖/提案/Run 原子提交与 PostgreSQL 唯一 active 约束。
- 能力索引、触发预选、L0～L3、5 个元工具、下一轮加载、6 项 LRU、4 轮自动卸载、会话 checkpoint 和每次模型调用披露记录；P1/P2 问答接入动态能力。
- 首批 visual_classify、paper_qualify、默认关闭的 figure_describe；13 类路由与 D38 校验；只读 Skill API 和令牌保护的管理员 reload；冻结 Manifest、Result、DisclosureContext、模型任务及 OpenAPI。
- 按用户补充需求通读 `/home/xq/source/paper` 并迁移可复用的 Markush 核心（保留 Apache-2.0 许可）、prime/氢/枚举边界适配、角色判断、mol_id 回填、MolParser Tools、公共提醒与分类修正规则。新增调用 Tool 的 Python markush_assemble，归档各类旧提示供 P4/P5 参考。
- 真实联调发现领域工具名含点号时上游拒绝请求；OpenAI 网关为传输名称生成稳定别名并还原调用，应用名称保持一致。

### 验证与范围

- 352 项单元测试、4 项隔离 PostgreSQL 集成测试、4 项真实模型冒烟通过；Ruff、format、mypy 通过。真实 GPT-6 Luna（Responses / 中等思考）完成发现、加载、实际分子拼接与作答。
- 样例 26 个视觉节点真实分类执行及缓存复用全部通过；参考一致率 21/26（80.8%）。参考为 Codex 视觉复核，用户/专家签核待补；多路线误判仍需改进。
- 报告：`samples/outputs/phase-3/20261001-183351/completion.md`；接口：`docs/interfaces/skills-and-capabilities.md`；代码地图：`docs/codemap/phase-3-codemap.md`。
- P4/P5 复杂图像检测/结构化、正式分子/事实投影仍为 planned；P3 提案可靠落库，但未冒充完整领域投影。Redis 通知经过替身验证，真实跨进程广播演练待补。graph、沙箱和用户私有 Skill 按计划留待后续。未初始化 Git，因此未创建 p3-done 标签。

## [0.2.0] - 2026-09-30 · P2 文档底座

对应计划：`docs/dev-plan/phase-02-document-foundation.md`、`docs/dev-plan/execution/phase-02-execution.md`。代码地图：`docs/codemap/phase-2-codemap.md`；冻结接口：`docs/interfaces/document-ir.md`、`docs/interfaces/openapi-p2.json`。

### 新增

- **领域模型**（`drugpaper/domain/`）：`ids.py`（ULID、`doc_{ULID}`、节点局部 ID 与全局引用 `{doc}/r{rev}/{local}`）、`geometry.py`（`BBox`/`Region`/`Geometry`，`pt_to_norm`、`local_to_page`、`page_to_local` 等换算）、`nodes.py`（`DocumentNode`、`Relation`、`EvidenceRef`、`QualityReport`、`DocumentMeta`、`IRDocument`）。
- **S2.0 数据库**：`persistence/models.py`（documents、parse_revisions、assets、nodes、relations、runs、artifacts、artifact_dependencies，全部带 `user_id` 默认 `default`）；`persistence/db.py`（异步引擎，会话设置 `app.current_user_id`）；Alembic 迁移 `0001_core`（PG 上按可用性创建 vector / pg_trgm 扩展与 schema `lg`）、`0002_text_index`（`nodes.tsv` + GIN，pg_trgm 索引）；`persistence/migrate.py`。迁移同时支持 SQLite（单元测试与本地开发）。
- **S2.1 BlobStore**：`storage/localfs.py`（`root/{user}/{ab}/{cd}/{sha256}`，临时文件 + `os.replace` 原子写入，并发写同一内容只留一份）、`storage/assets.py`（幂等登记资产）。
- **S2.2 MinerU**：`parsers/mineru_legacy/client.py`（按 legacy_mineru_merge 的真实接口：提交、轮询、拉取；客户端信号量背压；429 指数退避）、`paths.py`（拒绝 `..`、绝对路径、盘符）、`importer.py`（离线导入 `auto/` 目录；转存产物、登记资产、创建 parse_revision）。
- **S2.3 输入 Schema**：`parsers/mineru_legacy/schemas.py`（layout / middle 宽松模型，未知字段进 `source_ref.extra`）。
- **S2.4 IR Builder**（`parsers/mineru_legacy/adapter/`）：单元识别（成员同时按 `merged_from` 与 `belong_to` 反查、纯文本合并组解散、跨页组）→ 标签与类型（F2）→ 成员与图注（F3 `caption_mismatch`、F4 `absorbed_by_merge`、脚注标记字典）→ 续段与行级增强（F5/F6：middle.json 行中心落在空块内即认领为 continuation region，含 `cross_page` 行；无 middle 时推导并标 `derived`）→ 章节树（全大写标题为章节、句首小标题为 `.h0` heading、section_role 词典与继承、References 保留）→ 前置信息（标题、作者、Cite This、DOI，文件名兜底）→ 阅读顺序 → 质检（越界、IoU>0.6 重叠、资产缺失、页数、扫描页、页面尺寸不一致）→ 兼容启发式（大框包含小框、`legacy_id`）。样例论文快照 `tests/fixtures/jmedchem_2c00949/ir_snapshot.json`（261 节点，已人工核对）。
- **S2.5 IR 持久化**：`persistence/repos/{documents,nodes}.py`（`save_ir` 单事务批量写入、`load_ir`、`get_tree`、`get_node`、`children`、`nodes_on_page`、`context_window`）。
- **S2.6 渲染与裁剪**：`render/pages.py`（PyMuPDF；`render_page`、`crop` 按 `(pdf_sha, page, bbox, dpi, pad)` 缓存；`inspect_pdf` 取页数、页面尺寸、扫描页）。
- **S2.7 文本规范化**：`text/normalize.py` + `text/latex_rules.yaml` + `text/vocab.py`（LaTeX 转写、公式内逐字符空格合并、行尾连字符按词频合并、粘连词拆分、`D1 R` → `D1R`；每个字符记录原文区间，`map_span` 精确映射回 `text_raw`）。
- **S2.8/S2.9 检索**：`text/grep.py`（设计文档 7.9.3 节的 grep，`regex` 库带超时，norm 上匹配、raw 上兜底，`char_span` 为原文坐标，References 默认排除）；`text/index.py`（PostgreSQL FTS / pg_trgm / hybrid，SQLite 与无 pg_trgm 时 Python 实现）。
- **S2.10 Agent 接入**：`docsrc/ir_source.py`（`IrDocumentSource` 实现 P1 的 `DocumentSource` 协议）；工具包 `tools/builtin/doc_read.py`（get_outline / get_node / get_children / get_context_window / get_section / get_page_text）与 `tools/builtin/search.py`（grep / search）；`agent/qa.py` 的 `build_ir_qa_agent` 与 `resolve_evidence`（引用 → EvidenceRef：页码、region、char_span）；提示词 `agent/prompts/qa_system_ir.md`。AgentRuntime 的循环未改动。
- **S2.11**：`tracing/db.py`（`DbTracer` 把 Agent 运行与模型/工具/校验子 Run 写入 runs 表，可同时写 JSONL）；`agent/checkpoint.py`（PostgreSQL 上用 `AsyncPostgresSaver`，schema `lg`；否则内存）；`AgentRuntime.resume()`。
- **S2.12 管线**：`orchestration/pipeline_graph.py`（LangGraph：ingest → mineru_parse → build_ir → render_and_index；失败写 `status=failed` 与错误码；相同内容去重）；`services.py`（依赖装配）。
- **S2.13 API**（`drugpaper/api/`）：上传、列表、状态、树、页面图、页内节点、节点详情、裁剪图、检索、blob 代理；统一错误格式；OpenAPI 导出。
- **CLI**：`drugpaper db upgrade`、`drugpaper ingest [--offline] <pdf|auto目录>`、`drugpaper ask --doc <id> "问题"`（引用输出页码与区域）、`drugpaper resume --doc <id> <run_id>`、`drugpaper serve`。
- **测试**：单元测试 251 个（含 `tests/unit/ir/` 的 F1～F9、章节、快照；API 全链路；管线；检索；Agent 在 IR 上问答与崩溃恢复）；集成测试 3 个（`tests/integration/`，PostgreSQL：迁移升降、IR 入库与 FTS/trgm、runs 表与 checkpointer 跨进程恢复；本机用嵌入式 `pgserver`，CI 用 pgvector 服务）。
- **S2.14**：`scripts/verify_phase_2.py`（`make verify-p2`，全部验收项通过）；样例输出 `samples/outputs/phase-2/`（两个问答、IR 摘要、Table 3 裁剪图）。
- CI 增加 PostgreSQL 集成测试 job；Makefile 增加 `migrate`、`verify-p2`、`serve`。

### 变更

- `AgentRunResult` 增加 `evidence` 字段；`AgentRuntime` 增加 `resume()`，`run_start` 事件增加 `resumed`（均为只增不改）。
- `docsrc/textnorm.py`（P1 的轻量匹配规范化）改为委托 `text/normalize.py`：`strip_latex` 的输出随之变化（`4 ^ { \prime }` → `4′`、`D1 R` → `D1R`）。
- `Settings` 增加 `blob_root`、`mineru_url`、`render_dpi` 等；`database_url` 默认本地 SQLite；`.env` 中留空的值回落到默认值。
- 依赖：新增 sqlalchemy[asyncio]、asyncpg、aiosqlite、alembic、pgvector、pymupdf、fastapi、uvicorn、python-multipart、langgraph-checkpoint-postgres、psycopg[binary]、wordninja、regex；dev 新增 httpx（TestClient）、pgserver、types-regex。

### 与执行方案的偏差

- **本地数据库**：本机没有 Docker，单元测试与本地开发使用 SQLite（模型与迁移都兼容），PostgreSQL 专属功能（tsv、pg_trgm、`lg` schema、Postgres checkpointer）由集成测试覆盖，本机使用嵌入式 `pgserver`（PG 16.2 + pgvector，但**没有 pg_trgm**，trgm 走 Python 回退；CI 的 pgvector 镜像带 pg_trgm）。
- **偏移映射不入库**：`text_norm` 由确定性规则生成，偏移按 `attributes.norm_version` 随时重算，避免存储大数组。
- **`nodes.page` 冗余列**：按页查询用普通列加索引，代替设计文档 16.2 中基于 JSONB 表达式的索引。
- **合并组成员**：样例中大量成员没有 `belong_to`，成员以 `merged_from ∪ belong_to` 识别；纯文本合并组（如 p8 的 `p008.g003`）解散为原始块。
- **图注成员混入正文**：`p002.g001.c0`（Scheme 1 图注）的行级证据落在正文空块内，说明 MinerU 把正文续段并入了图注。本阶段认领续段并标记 `caption_absorbed_text`，文本拆分留给后续。
- **PDF 页面尺寸优先**：`build_ir` 接收 PDF 真实页面尺寸做归一化（样例中与 layout 一致；不一致时记 `page_size_mismatch`）。
- **测试位置**：执行方案把 MinerU 客户端测试放在 `tests/integration`，这里用模拟传输层放在单元测试（不访问外部服务）；真实服务的端到端提交需手动验证。
- **会话中的 `app.current_user_id`** 用 `set_config(..., false)`（会话级），P7 启用 RLS 时需改为事务级并配合连接池复位。

### 安全与合规提示

- **API 没有鉴权与用户隔离**（P7 补上），`drugpaper serve` 默认只绑定 127.0.0.1，只应在本机或受信任的内网运行；`/blobs/{ref}` 仅凭 sha256 即可访问内容。
- **PyMuPDF 为 AGPL-3.0 / 商业双许可**（设计文档指定使用），对外分发或提供网络服务前需确认许可合规；如有顾虑可替换为 pypdfium2，渲染接口只在 `render/pages.py` 一处。

### 未完成 / 待办

- 真实 MinerU 服务端到端提交（`drugpaper ingest paper.pdf`，需要 `MINERU_URL`）与真实模型冒烟（`verify_phase_2.py --smoke`，需要 `.env`）未在本机运行，问答验收使用 FakeLLM 离线脚本。
- 只有 1 篇样例论文；执行方案要求的"另外 2 篇论文续段人工抽查"与 `tests/fixtures/extra/` 待补充。
- 仓库仍未初始化 Git，分支与 `p2-done` 标签待团队创建。

## [0.1.0] - 2026-09-30 · P1 最小 Agent Loop

对应计划：`docs/dev-plan/phase-01-minimal-agent-loop.md`、`docs/dev-plan/execution/phase-01-execution.md`。

### 新增

- **S1.0 工程骨架**：`pyproject.toml` + `uv.lock`（Python 3.12，依赖全部锁定）；`Makefile`（up/down/lint/test/test-int/smoke/verify-p1）；`.env.example`；`.gitignore`；`.gitattributes`（fixtures 大文件走 Git LFS）；`.pre-commit-config.yaml`（ruff、ruff-format、mypy、单元测试）；`docker-compose.yml`（pgvector/pg16、redis:7，带健康检查，本阶段不使用）；`.github/workflows/ci.yml`（安装 → lint → test）。ruff 规则 `E,F,I,B,UP,ASYNC,SIM`、行宽 110；mypy 对 `drugpaper/` 启用 strict。
- **S1.1 配置**：`drugpaper/config/settings.py`（`Settings`、`get_settings()`）；`config/models.yaml`（设计文档 18.2.2 格式的最小子集：1 provider / 1 model / 1 pool / 1 stage / 1 task）；`drugpaper/config/models_file.py`（YAML 加载、`${VAR}` 与 `env://VAR` 展开、按 task→stage→pool→model→provider 解析为 `ResolvedModel`；缺失变量时指出变量名和配置位置）。
- **S1.2 消息与请求模型**：`drugpaper/gateway/base.py`（`Message`、`TextPart`、`ImagePart`、`ToolCall`、`ToolSchema`、`Usage`、`ChatRequest`、`ChatResponse`、`ModelClient` 协议、5 类类型化异常）。
- **S1.3 OpenAI 兼容客户端**：`drugpaper/gateway/openai_compat.py`。进程内复用连接池；`base_url` 自动补 `/v1`；图片按实际字节识别 MIME；工具参数非法 JSON 时标记而不抛异常；超时 / 429 / 5xx 指数退避重试 2 次（带抖动），其他 4xx 不重试；`json_schema` 不被支持时降级为 `json_object` + Schema 说明，并按模型名缓存；结构化输出不合法时带校验错误修复重试 1 次；`thinking=False` 时关闭 Qwen 思考输出并剥离 `<think>` 块；记录 token、图片数、延迟和实际模型名。
- **S1.4 Tool 协议与注册表**：`drugpaper/tools/protocol.py`（`ToolSpec`、`Tool`、`ToolContext`、`ToolResult`、`@tool` 装饰器、精简 JSON Schema 生成）；`drugpaper/tools/registry.py`（重名检查；`invoke` 把未知工具、参数错误、超时、异常统一转为 `ok=False`；输出超长截断并只保留可见 refs）。
- **S1.5 文档数据源与工具**：`drugpaper/docsrc/base.py`（`DocumentSource` 协议、`BlockView`、`Hit`）；`drugpaper/docsrc/layout_json.py`（`LayoutJsonSource`：`p{page}.b{detailed_index}` / `p{page}.g{confirmed_index}` 块 ID、图表块取图注、空块跳过、bbox 归一化、正则检索失败时退回字面量）；`drugpaper/tools/builtin/layout_doc.py`（`list_blocks`、`get_block`、`grep`）。
- **S1.6 状态、预算与扩展点**：`drugpaper/agent/state.py`（`Budget`、`AgentState`、`AgentAnswer`、`Citation`、`AgentRunResult`）；`drugpaper/agent/interfaces.py`（`ToolsetProvider`、`ContextAssembler`、`AnswerValidator`、`AnswerSanitizer`、`Tracer`）；`drugpaper/agent/defaults.py`（`StaticToolset`、`SimpleAssembler`、`NoopValidator`）；`drugpaper/agent/config.py`（`AgentConfig`）。
- **S1.7 AgentRuntime**：`drugpaper/agent/runtime.py`。LangGraph 图 `assemble → call_model → execute_tools / validate / force_finalize`；`submit_answer` 作为结束方式，纯文本回答再做 1 次结构化转换；并行工具执行；步数 / 模型调用 / 工具调用 / 耗时四重预算，用尽后强制作答或给出兜底答案；校验失败反馈重答 1 次；整体超时与取消事件；内存 checkpointer（`thread_id = run_id`）。
- **S1.8 引用校验**：`drugpaper/agent/validators.py`（`CitationValidator`：引用必须来自工具结果、quote 必须是原文子串、答案非空必须有引用；`sanitize` 删除无效引用）。
- **S1.9 Tracer**：`drugpaper/tracing/jsonl.py`（`JsonlTracer` 每个 run 一份 `runs/<run_id>.jsonl`，字符串只保留 200 字摘要；`CompositeTracer`、`NullTracer`、`read_events`）；`drugpaper/tracing/console.py`（`ConsolePrinter`，rich 树状实时输出）。
- **S1.10 CLI 与提示词**：`drugpaper ask <layout.json> "<问题>" [--max-steps] [--max-model-calls] [--max-tool-calls] [--max-wall-time] [--verbose] [--json]`；`drugpaper/agent/prompts/qa_system.md`；`drugpaper/agent/qa.py`（CLI、冒烟测试、验证脚本共用的问答 Agent 装配）。
- **S1.11 测试**：`tests/fakes/fake_llm.py`（`FakeLLM`、`ListTracer`、脚本构造函数）；`tests/fakes/mock_http.py`（基于 httpx2 MockTransport 的模拟端点）；单元测试 89 个（settings、gateway、openai_compat、tools、layout_doc、validators、runtime、tracing、cli）；`tests/smoke/test_live_model.py`（`--smoke` 时运行）。
- **S1.12 接口冻结与阶段验证**：`docs/interfaces/agent-runtime.md`；`scripts/verify_phase_1.py`（lint、单元测试、可选冒烟、`max_steps=1` 问答、两个冒烟问题输出到 `samples/outputs/phase-1/`、验收清单）。
- 合成测试数据 `tests/fixtures/mini_layout/sample_layout.json`（按设计文档 2.2 节结构构造，非论文原文）。
- 代码地图 `docs/codemap/phase-1-codemap.md`。
- **真实样例论文**：`tests/fixtures/jmedchem_2c00949/`（复制自 `legacy_mineru_merge/test/10_1021_acs_jmedchem_2c00949/auto`）；`tests/unit/test_real_paper.py`（文档结构、合并组、S1.5 三项验证、LaTeX 匹配、quote 容错、FakeLLM + 真实论文端到端问答）。
- **轻量文本规范化** `drugpaper/docsrc/textnorm.py`（`strip_latex`、`compact_key`），只用于匹配，返回给模型的仍是原文：
  - `grep` 在原文不命中时，再在去 LaTeX 的文本上匹配；
  - `CitationValidator` 的 quote 比较在归一化比较失败后，再用"去 LaTeX、去空白与连字符"的键比较。

### 修复

- 真实论文中大量文本是无分隔的 LaTeX（F7），例如摘要中的 `\mathrm { D } _ { 1 }receptor`。修复前 `grep("D1 ?receptor")` 命中不到摘要块，模型把原文写成正常文本（`D1 receptor`、`dopamine replacement`）作为 quote 时会被误判为不匹配、触发无效重答。修复后两者都能正确匹配，错误的 quote（如把 26 写成 27）仍会被拒绝。

### 与执行方案的偏差

- **HTTP 模拟库**：`openai` 3.x 的底层 HTTP 库已换成 `httpx2`，`respx` 只能拦截 `httpx`，无法使用。改为 `tests/fakes/mock_http.py`（httpx2.MockTransport），覆盖 S1.3 表格中的全部用例；依赖中去掉 `httpx`、`respx`，新增 `httpx2`，并显式声明 `langchain-core`（运行时直接导入 `RunnableConfig`）。
- **AgentState.messages**：使用 `Annotated[list[dict], operator.add]` 存放 `Message.model_dump()`，而不是 LangGraph 的 `add_messages`（后者面向 LangChain 消息类型）。这样 checkpointer 序列化不依赖自定义类型，P2 换 Postgres checkpointer 时无需改动。
- **图的额外边**：`call_model` 增加 `error → END` 分支，用于模型调用失败、结构化转换两次失败时直接结束（`stop_reason=error`）。
- **StaticToolset** 签名为 `StaticToolset(registry, names)`，因为 `tools_for()` 需要返回 `Tool` 对象。
- **AgentConfig** 放在 `drugpaper/agent/config.py`（避免 `interfaces.py` 与 `defaults.py` 循环导入），并增加 `temperature`、`max_tokens`、`model_timeout_s`、`max_validation_retries`。
- **ToolCall** 增加 `raw_arguments`、`arguments_error`，**ToolContext** 增加 `parent_run_id`，**ModelError** 增加 `retryable`、`status_code`；新增可选接口 `AnswerSanitizer`。均为只增字段。
- **model_call 事件**用 `phase`（agent / convert / force_finalize）区分调用类型，避免与事件类型字段 `kind` 冲突。
- **stop_reason**：重答后仍有校验问题时为 `validation_failed`（答案保留、无效引用删除、`warnings` 非空）。

### 未完成 / 待办

- 本机没有 `.env`，真实端点冒烟测试（`make smoke`）未运行；`samples/outputs/phase-1/` 当前保存的是 FakeLLM 在真实论文上的离线演示结果（文件名带 `.offline`）。
- 真实数据暴露的 P2 问题（P1 不处理）：标题等块中的 LaTeX 残留（如 `\bullet ^ { \prime }`）直接返回给模型；Table 1 合并组的文本是母核标签 `1-10`，真正的标题在成员块 `p003.b000`（F3）。
- 本机未安装 Docker 和 make：`docker compose` 健康检查未验证；`Makefile` 目标请在 Linux/macOS 或 CI 上使用，Windows 下可直接运行 `uv run python scripts/verify_phase_1.py`。
- 仓库尚未初始化 Git，分支 / 标签 `p1-done` 需由团队按 README 中的分支约定创建。


## [Unreleased] - 2026-10-03 · 结构化回填交付修正

- 默认结果页展示编号回填后的结构化数据及JSON下载，SAR按行展示完整SMILES、分子式和文献测定性质。
- 修复sar_record覆盖拼接结果、FigureAgent未交付SAR返回；保持产品/状态，按行证据关联性质，阻止未知取代基被Unclear拼接。母核模板不提供成品分子式，未完整拼接的SAR保持needs_review。
- 真实Table1位置变量暂不支持拼接：10行各3项测定保留，完整结构明确未完成；不以检测绑定完整冒充完整SAR交付。


## [Unreleased] - 2026-10-03 · 全图表结构化回填交付

- 将真实测试默认范围改为文献全部图表；当前17张均保存结构化结果，当前含化学结构图有回填，历史初版保留。
- 支持有明确拓扑约束的联芳醛位置变量，以及R端点/Y芳香CH-N/X明确位置替换。两张SAR表10+16行完整连接结构对照通过，共78项逐行性质；不猜未知基团、未明确位置或立体构型。
- 新增文档内mol.lookup_references，3D对接配体按明确编号绑定结构；唯一手性中心的R/S从明确名称推导并标记来源。回填先保存，再处理性质；错误复核清单不能破坏已有数据。
- 最终694项单元/集成测试通过（128.46秒），Ruff/格式及mypy通过。曲线-only误检及部分曲线自检问题仍保留待复核，不宣称整篇内容已独立通过。

## [Unreleased] - 2026-10-03 · Skill 驱动的通用化合物实例

- 新增 SAR 行、流程图变体和分子展示图共用的 ID-only 化合物实例契约。视觉模型只输出编号、变量、印刷标签与证据；受校验的识别结构由 mol.deliver 通用 E-SMILES 拼接，完整产物带来源并持久化为独立结构/标签事实。
- SAR 根据原始行号关联指标，分片合并重算行号；正常 Agent 要求明确实例，拒绝用旧的变量列猜测完成新链路。全图像回填保留检测对象与失败原因，线程计算不共享图像编号索引。
- 路线基于最终实例投影；同框多标签也视为系列，无证据不得两两组合。明确单中心 R/S 由代码推导并标记来源；端点绑定实际完整实例。错误 attachment 与缺失定义失败，不任意修补位置或挑选多个产物之一。
- 实际运行暴露跨箭头配对错误；加入模型网关语义校验、精确错误字段与定向复核。历史错误输出、原始调用和独立审查保留；真实内容准确性不能由执行完成或模型 ok 替代。
- 模型修复后仍错配箭头时，只对结构 Schema 合法的响应隔离错误连线，保留 rejected_variant_links 和 semantic_errors；有效分子继续回填，报告仍为 needs_review。新增实际 FigureAgent 恢复测试；缺失分子来源不能被恢复为成功。
- 真实内容审查发现硫中心的旧 CIP 分配与现代算法不一致，已统一现代 CIP，并在最终标准化图再次校验标签；新增3类硫中心测试。保持真实视觉输出不变重执行生产 Skill，6实例完整且全部显式 R/S 一致，2条错误连线仍待复核。
- 最终725项单元/集成测试通过（149.54秒），Ruff/452文件格式与mypy/234源码通过；API 与 Worker 已更新，Detector/审阅服务保持运行。整篇/全部SAR图像仍未作独立准确性认证。
