# Drugpaper 开发工作台

基于当前 P2–P6 后端接口的前端项目。可浏览已有文档及分析产物，不需要先恢复 MinerU/MolParser。P5/P6 真实文献验收和完整 P7/P8 功能仍按各自计划推进。

## 2026-11-20 基础调试版本

前端需求统一见[总体设计 17.6](../docs/drug-paper-agent-detailed-design.md#176-开发调试工作台已确认需求2026-11-20待实现)，步骤与验收见[P8 执行方案](../docs/dev-plan/execution/phase-08-execution.md#2026-11-20-新增执行范围全过程开发调试待开发)。下列基础入口已接入现有项目；完整 P8 与真实内容验收仍按原计划推进：

- 阶段细分栏目与全流程工作区共用选择状态；主区左侧原始 PDF，右侧过程详情/编辑。MinerU 默认只渲染合并后图表框，独立文本可查看 OCR；跨页图表作为整体，暂不做父子图交互。
- 自动模式上传后执行 MinerU 及全部图表解析；手动模式仅执行 MinerU，点击单图解析后自动分类、选 Skill、执行。保留独立分类和指定 Skill 的调试入口，分子默认按需处理。
- 每一步查看实际输入输出、图片、模型请求响应、工具调用、Skill/代码版本、耗时及错误；支持执行到、执行前暂停、继续及从某步重跑，复用有效上游并保留历史比较。
- 点击分子框在右侧显示分子式、SMILES、标准结构图（不可渲染则截图）；可增删调框、改单分子 SMILES，保留并可恢复机器原始值，重跑默认采用人工值。还可修正图表类型并重新路由。
- 编辑完整 Skill 包和相关公共工具代码，先当前文献当前图表局部试运行，再主动更新全局；不能用全局源文件直接覆盖冒充局部版本。
- 单图失败不阻塞其他图表，显示逐图状态与文档汇总，可只重试问题图。暂不加入单元格/提取字段/分子关联人工编辑。

当前专用接口位于 `/documents/{id}/workbench`，运行、事件和不可变源码快照保存在开发数据目录的 `workbench/`，化学结果仍使用现有数据库/Artifact。独立进程执行局部代码，单个数据目录最多同时运行3张图（SQLite开发库串行执行）；只适合单机受信任的开发环境，不替代通用 P8 Correction/rerun 或分布式任务管理。

使用方式：进入「全流程工作区」或某个阶段，选择 PDF 上的图表，点击解析；在执行范围中选择暂停点，检查后创建继承上游的新运行。分子可直接在 PDF 绘制新增/调整框；右侧可修改 SMILES、删除框或恢复机器结果。修改 Skill 后先保存局部版本、校验、试运行，再主动更新全局。请求/响应中的图片保存为 Blob，可在步骤详情查看；长记录可完整导出。全局公共 Python 工具源码发布后，常驻业务进程需重新加载；新的工作台子进程直接使用新代码，历史运行保留原快照。

需要真实解析服务时，显式启用连接模式（仍使用隔离开发数据库，启动不执行推理）：

```bash
DRUGPAPER_DEV_CONNECTED=1 DRUGPAPER_DEV_WEB_HOST=0.0.0.0 npm run dev:offline
```

默认离线入口继续关闭 MinerU/MolParser；连接模式从本地配置读取 MinerU、MolParser、OPSIN、YOLO 服务地址。独立调试进程直接连接服务，避免继承开发终端的 HTTP/SOCKS 代理。上传 PDF 的自动/手动模式选择会保存在文献元数据中；服务不可用时显示错误，不生成成功结果。

验证材料见 `samples/outputs/frontend/20261120/`：受控图表完整链路与独立子进程、本地版本隔离/失效、模式/失败隔离、分子修正与恢复、真实 API 只读浏览。用户随后明确授权仅测试一张样例图，真实分类、分子检测识别、暂停及继承上游后的编号/结构化提取验证通过：识别1个形式有效结构，第二次运行检测/识别调用均为0。记录见 `live-stage-run.json`；这不作为整篇或内容准确率通过。

## 一条命令离线联调

项目根目录已安装后端 `.venv`、前端依赖后：

```bash
cd /home/xq/source/agent_source/web
npm ci
npm run dev:offline
```

打开 http://127.0.0.1:5173 。该命令同时启动独立后端（18001）和前端（5173），将已有论文解析产物导入 `data/workbench-dev/` 中的隔离 SQLite 和文件目录；重复启动复用，不删除数据，不访问业务数据库。Ctrl+C 关闭两端。可用 `DRUGPAPER_DEV_API_PORT`、`DRUGPAPER_DEV_WEB_PORT` 更换端口。

该入口不会自动执行模型推理；原样例没有的化合物、图表、全文结果会显示为空，不制造成功结果。页面中的显式调试操作仍可能调用模型，应核对请求后执行。上传新的PDF仍需配置MinerU；离线联调入口不提供这项远程服务。

局域网其他机器访问时，指定前端监听地址，后端仍由同源代理转发：

```bash
DRUGPAPER_DEV_WEB_HOST=0.0.0.0 npm run dev:offline
```

本机当前局域网访问地址为 `http://10.154.24.41:5173/`，API文档可从同一地址的 `/docs` 打开；只需要访问前端5173端口。`DRUGPAPER_DEV_WEB_HOST` 默认仍为 `127.0.0.1`。

也可分别启动：

```bash
# 终端1，项目根目录；默认新建临时库，--data-dir 可指定专用持久目录
.venv/bin/python -m scripts.dev_workbench --port 18001 --fixture tests/fixtures/jmedchem_2c00949

# 终端2
cd web
DRUGPAPER_API_TARGET=http://127.0.0.1:18001 npm run dev
```

## 连接已有开发后端

```bash
cd web
cp .env.example .env.local
# 将 DRUGPAPER_API_TARGET 改为对应后端地址，例如 http://127.0.0.1:18000
npm run dev
```

Vite同源代理 `/api`、`/healthz`、`/docs`、`/openapi.json`；配置变更需重启前端。远程开发可将5173端口SSH转发到本机。生产构建 `npm run build` 输出 `web/dist/`，正式托管需要同源反向代理这些API路径。浏览器不保存后端密钥。

## 已接入的工作流

| 页面 | 当前功能与接口 |
|---|---|
| 全流程/分阶段调试 | `/workbench`；合并布局、单图/分步执行、过程日志、暂停/继承重跑、分子修正、局部包编辑与版本比较 |
| 文档与证据 | 文档列表/上传、树、页面图、节点详情；页码/DPI/缩放/区域开关、原文证据高亮 |
| 化合物台账 | `/compounds` 分页与详情；已采纳结构、候选来源、冲突、角色状态、证据；RDKit本地WASM绘制 |
| 测定数据 | `/measurements` + `/assays`；按化合物/指标/状态筛选、原值/单位、物种/条件、证据 |
| 图像报告 | `/figure-reports`；历史报告筛选、原图/表格/曲线、自检/未确认项、Skill过程和Run跳转 |
| 论文检索 | `/search`；hybrid/fts/trgm/grep，字面或正则；点击命中回跳来源页 |
| 全文结果 | `/full-text`；projection.delivery分层字段、审计证据、警告、Run定位、JSON下载；404显示尚无有效结果 |
| 阅读检查点 | `/reader-notes`；页版本、本页增量、累计阅读/调用计数、疑问/回查答案及证据 |
| 运行监控 | `/runs` 与单条详情；筛选、时间线、父Run/Trace、inputs/outputs/model/metrics/error |
| 能力与版本 | `/skills/catalog` 和 `/skills/{name}/versions`；只读检查当前可见能力与注册版本 |
| 批次状态 | `/batches/{id}`；输入已有批次ID轮询查询；后端未提供批次列表，不伪造列表 |
| 调试操作 | Reader/重读、DocLookup、全文 prepare_text/force、导出ZIP；默认关闭写操作，确认实际参数后提交 |
| 请求调试台 | 最近100条API请求，URL/耗时/状态/参数/响应、脱敏导出；区分成功、HTTP错误、超时、中止和网络错误 |

证据会核对当前文档和revision，历史证据提示版本差异，避免错跳。后端char_span按Unicode码点计数，前端正确处理含非BMP字符的原文。表格单元格有归一化bbox时可在来源裁剪图高亮；返回论文页时定位到来源表格节点。图表仅绘制已存数值点，保留linear/log10轴，不从像素坐标猜测测定值。

## 调试约定

- 默认3秒轮询，可暂停、切换1/10秒，后台页停止周期刷新。GET切换资源时中止旧请求并清理旧数据；断线显示错误，监控保留并注明最后快照。
- Run覆盖历史revision，running不代表进程存活；调用数按单条Run查看，避免父子累计值重复求和。无后端心跳、GPU监控、token事件流或取消接口时不伪造。
- 页面仅将 `doc` 和 `view` 保存在URL，可直接打开某文档的数据页面；具体节点选择/Run焦点在本会话中保留。
- 图片请求与WASM资源请在浏览器Network检查；API请求在调试台记录。原始JSON预览有限长并屏蔽常见凭据。领域数据JSON下载保留完整项数和长值，同时屏蔽常见凭据；网络导出保留捕获时的有界响应。
- POST不自动重试；超时、关闭页面或切换文档不取消后端任务，先检查Run。完整鉴权与模型管理依赖P7；交互Chat和通用Correction/rerun预览依赖后续P8；基础图表调试使用独立workbench接口。本轮不提供假登录或SSE。

## 自主验证

```bash
npm run build
npm test
npm run format:check
npm run test:e2e
# 验证生产构建（含本地RDKit资产加载），需先build：
WORKBENCH_TEST_PREVIEW=1 npm run test:e2e
# 已启动 dev:offline 后，验证实际HTTP、离线论文检索与证据定位（无写请求）：
npm run test:http
```

若本机已有Chromium，可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/absolute/path/to/chrome`。截图可通过 `WORKBENCH_SCREENSHOT_DIR` 指定输出目录；HTTP测试使用 `SMOKE_BASE_URL`、`SMOKE_OUTPUT_DIR` 指定地址与结果目录。

模拟接口测试显式标记受控样例，覆盖真实接口结构；实际HTTP测试使用现有离线论文，不访问MinerU/MolParser或真实模型。验证证据见 `samples/outputs/frontend/20261116/`。

## 文本与公式预览

点击布局中的文本块，右侧显示 OCR 排版预览；MinerU `middle.json` 的文本/公式 span 按原顺序渲染，支持行内及独立 LaTeX 公式。没有结构化片段时，识别 `$…$`、`$$…$$`、`\(…\)`、`\[…\]`，公式节点可直接渲染裸 LaTeX。字体及排版库在本地加载。

“原始 OCR / 公式源码”可展开核对；不支持的公式保留源码并提示失败（悬停查看错误），预览不修改 OCR 或证据字符偏移。现有导入文献直接从对应 revision 的原始 middle 资产按布局坐标校验后读取片段，无需重新解析；缺失或不对齐时降级到原文并提示。该预览不修复识别错误。

## 单图结果文件与JSON查看

单图完成回填后自动保存独立JSON，报告完成后保存带检查结论的版本。在单图调试页“回填后的解析结果文件”或“图像报告”中下载；已有报告首次读取时补存，无需重新调用模型。文件包含schema_version、来源文献/节点/运行/报告、processing_stage、status、最终回填extraction及quality，不混入请求日志或回填前快照。pending_review表示只完成回填，needs_review保留已发现的问题；每次运行独立文件与内容寻址Blob保留历史。

通用JSON展示使用本地打包的CodeMirror代码查看器：完整格式化JSON、行号、字段/值语法高亮、括号匹配、代码折叠、Ctrl/Cmd+F查找、自动换行和完整复制。展示为只读，不改写后端结果；数组不分页截断，长文档按编辑器视口渲染，查找覆盖全文。局域网下剪贴板不可用时降级复制/全选；显示及复制仍屏蔽常见凭据，领域JSON下载保持完整。运行时不依赖外部CDN。
