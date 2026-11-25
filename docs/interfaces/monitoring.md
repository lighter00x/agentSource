# P8 early：只读开发监控 API

现有 FastAPI 应用包含 `monitoring.router`；无需新表或迁移。沿用 P6 default 用户边界，无完整 P7 鉴权，适用于本机开发。

## 接口

- `GET /api/v1/documents/{document_id}/runs`：`status`、`component`、`trace_id` 为可选精确筛选，`limit=50`（1–200）、`offset=0`（≥0）。返回 `runs,total,limit,offset,observed_at,scope`。按 started_at、id 降序。scope 固定 `persisted_history_all_revisions`。
- `GET /api/v1/documents/{document_id}/runs/{run_id}`：归属一致的单条详情。
- `GET /api/v1/documents/{document_id}/reader-notes`：`limit/offset` 同上，按页升序，返回当前 active_revision 的 active 记录及总数、revision、notice。每条包含 id/page/version/fingerprint/created_at/payload。

Run摘要包含 id/document_id/run_type/component/component_version/parent_run_id/trace_id/status/error/metrics/started_at/ended_at/duration_ms。详情额外包含 component_hash/inputs/outputs/model/debug_notice。

所有接口先检查文档存在、default 归属及未删除；单条Run再次核对归属及文档ID；不可见返回404，参数越界422。没有新写操作、任意SQL或环境配置读取能力。

## 解释方式

- 时间以ISO8601返回，SQLite无时区时间按UTC解释。未结束Run的duration_ms为观测时长，非进程心跳。
- RunRow无统一revision字段，列表明确含历史记录；不能把Trace或组件时间顺序推断成完整调用图。
- model/metrics保留原始计量，不跨父子累计求和。
- Reader笔记查询只筛选revision/active，不验证当前组件源指纹；能否恢复由正式Reader校验。
- 递归屏蔽常见凭据键及Bearer/sk令牌，详情限深16、列表/对象200项、字符串16000字符，截断有标记。不是通用内容脱敏器。
- 轮询只能看到已提交数据库记录。事件流、模型token流、worker/GPU存活、取消与重跑规划不由这些接口提供。

验证：`tests/unit/test_monitoring_api.py`；前端用法与调试步骤见 `web/README.md`。

## 2026-11-20 全过程调试的监控扩展需求（待实现）

见[总体设计](../drug-paper-agent-detailed-design.md) 13.7/17.6 和[P8 执行方案](../dev-plan/execution/phase-08-execution.md)。以上只读 API 继续描述现状，不能视作下列能力已交付：

- 文献/图表/运行/步骤关联，区分计划 Skill 与实际调用；逐步持久化开始、结束、失败、暂停、继续及复用来源，而非仅在最终提交后可见。
- 逐步输入输出、原始及编号/裁剪图片、实际模型请求响应、工具参数返回、包/公共代码快照、耗时与错误。凭据服务端脱敏；长内容通过受归属检查的资产入口按需查看，摘要截断明确标识。
- 显示源 revision、上游运行和 Artifact/人工修正版本，以及失效下游；记录逻辑模型任务与网络重试的不同口径，不累加父子重复统计。
- 持久化执行到/暂停前目标、暂停点与阶段状态；新运行继承有效上游并可比较，不能从时间线推断完整依赖。
- 文档汇总未处理、成功、失败、待复核等逐图状态，单图异常不阻塞其他图表。实际状态 Schema 和过程查询接口待开发冻结；running 仍不等同于进程存活。


## 基础工作台接口（2026-11-20）

已接入 `workbench.router`，前缀 `/api/v1/documents/{document_id}/workbench`。以上 early 只读 API 保持原义；扩展需求已有下列基础入口，不将整套P8或分布式能力视作完成。所有请求沿用当前 default 用户边界，核对文档归属、删除状态与 active revision；图表引用必须属于当前文档版本。

| 方法与路径 | 内容 |
|---|---|
| GET 根路径 | 最终逻辑图表、跨页 regions、阶段目录、类型枚举、最新运行与汇总 |
| GET `/layout?page=0` | 默认布局投影：隐藏图表内部原始成员框，保留独立文本/OCR；原始来源从节点详情查看 |
| GET `/runs?node_ref=...`、`/runs/{id}` | 历史摘要、逐步状态/输入输出、实际请求响应与工具/图片事件、不可变版本指纹 |
| POST `/runs` | `node_ref`；默认自动分类路由解析，可选 `source_run/start/stop_after/pause_before/skill/force` |
| POST `/runs` 的预处理选项 | `operation=detect|recognize|recognize_one`，单分子使用 `molecule_ref`；默认 `parse`。`reuse_molecules=true` 配合 `source_run` 复用有效预处理后自动分类解析 |
| POST `/runs/{id}/pause` | 请求当前阶段结束后暂停；继续使用新运行及 `source_run/start`，保留原运行 |
| GET `/runs/{id}/file?path=...` | 此运行实际使用的包/公共工具源文件快照 |
| GET `/molecules?node_ref=...` | 分子框、结构、分子式/SMILES、人工覆盖及修正版本/历史 |
| POST `/corrections` | `kind=type|box|smiles|restore`；`node_ref/expected_version`，按类型附分子引用、bbox/segment、SMILES或image_type；返回失效起点 |
| GET/PUT `/files` | 按图表读取全包/公共代码目录与文件基线；保存局部草稿，写操作含 `expected_version` 防丢失更新 |
| POST `/validate` | 校验已保存局部包、Python语法和路由；不声称行为或化学准确率通过 |
| POST `/publish` | 显式更新全局源码；需要当前草稿有整图试运行记录，保留发布前源码；公共Python代码需常驻业务进程重新加载 |
| POST `/auto` | 为未完成/问题图逐图创建独立运行，一图失败不阻塞其余图 |

文档上传另支持 `mode=manual|auto`，兼容原 `understand` 行为；模式显式给出时采用工作台流程，手动仅解析。默认离线工作台仍不连接解析服务，`--connected` 才启用本地配置。

工作台控制记录位于 blob_root 同级 `workbench/`，写入采用原子替换并保留草稿/修正/运行版本；化学产物使用既有表。复制固定包和共享代码后，以新Python进程装载，不注册局部版本到全局；缓存纳入生效代码。进程槽位最多3个（SQLite开发库串行），当前图表执行时禁止并发修改。无效或变化的上游返回409，不静默复用；已退出进程显示 interrupted，可从已保存的有效步骤创建新运行。

事件中的请求正文与SDK超时选项分开，模型重试逐次记录，凭据在服务端屏蔽；图片转Blob后可查看，不在事件中堆放Base64。界面摘要可能截断，完整记录通过专用响应/导出获取。单机文件及进程锁不是跨主机执行保证，原 running 非心跳的解释仍有效。

验证入口：`tests/unit/figure/test_workbench.py`、`tests/unit/test_model_failover.py` 的传输日志用例、`web/tests/browser/stage-debug.spec.ts`；受控回放与真实API只读材料见 `samples/outputs/frontend/20261120/`。
