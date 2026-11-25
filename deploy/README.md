# 本机部署与运维

首次部署：2026-10-01；P4 升级：2026-10-02，应用 v0.5.0。服务通过 systemd 用户服务管理，用户 xq 的 Linger 已开启，无需保持终端登录。

| 服务 | 地址/配置 | 运行方式 |
|---|---|---|
| 应用 API | http://127.0.0.1:18000/docs | drugpaper-api.service，2 个 API Worker |
| YOLO 检测 | http://127.0.0.1:8010/detect | drugpaper-detector.service，1 个共享 Worker |
| GPU | 物理 GPU 0，RTX 3090 | CUDA_VISIBLE_DEVICES=0，YOLO_DEVICE=0 |
| PostgreSQL | 127.0.0.1:15432/drugpaper | 既有容器，迁移至 0007_figure_agents |
| Redis | 127.0.0.1:16379，应用 DB 10、broker 11、results 12 | 复用既有容器 |
| MinIO | 127.0.0.1:19000，控制台 19001 | 既有容器；应用当前仍使用 LocalFsBlobStore |

8000 端口已有其他应用，因此本项目使用 18000。API 与检测 Worker 绑定本机回环；完整 HTTP 用户认证仍属 P7，当前没有部署外网入口。

## 环境与资产

- `.venv`：应用、文档解析、问答依赖。已有 CPU torch 不承担检测服务的推理。
- `.venv-yolo`：独立 GPU 推理环境；torch 2.6.0+cu124、torchvision 0.21.0+cu124、ultralytics 8.4.171。实际 GPU 运算和 YOLO 已验证。安装组合来自 [PyTorch 官方版本表](https://docs.pytorch.org/get-started/previous-versions/)。完整实装版本见 yolo-gpu-freeze.txt。
- `.env`：保留原模型/数据库/外部服务配置；检测切换 remote、GPU 0，管理员和内部检测令牌已生成，权限 0600。
- `deploy/runtime.env`：仅部署项与内部令牌，权限 0600，已加入忽略规则；不提交、不打印。systemd 不直接解析包含 dotenv 变量引用的整个业务 .env。
- 权重：`data/models/moldet_v2_yolo11n_640_general.pt`，从旧项目复制并验证 SHA256，运行不再依赖旧项目目录；摘要 f309cc4f491d1bf67edb5eb9cfd4187d7a4648adb280bb9664ec56b9fc3ca25a。
- 持久资产：`/home/xq/projects/drugpaper-infra/shared/blobs`、`shared/runs`；本机 API/CLI 使用相同配置。
- 迁移前备份：`/home/xq/projects/drugpaper-infra/backups/postgres/drugpaper-20261001T151335Z-3922941.dump`。迁移没有清空业务数据。

## 管理服务

工作台常驻配置（2026-11-24）：`drugpaper-web.service` 在 `0.0.0.0:5173` 提供前端，代理到 `drugpaper-workbench.service` 的 `127.0.0.1:18001`。工作台使用连接模式及原有 `data/workbench-dev/` 数据，不在启动时导入样例或执行推理。业务 API 仍使用独立的 18000 端口。

前端、工作台 API 和业务 API 均设置 `Restart=always`、`RestartSec=5`、`StartLimitIntervalSec=0`，进程退出后持续重试；主动执行 `systemctl --user stop` 仍会停止服务。已启用开机启动和用户 Linger，关闭终端、退出登录后保持运行。机器关机期间无法提供服务，开机后自动启动。

```bash
# 本机安装/重装服务链接；Linger 已开启。
systemctl --user link /home/xq/source/agent_source/deploy/systemd/drugpaper-web.service /home/xq/source/agent_source/deploy/systemd/drugpaper-workbench.service
systemctl --user daemon-reload
systemctl --user enable --now drugpaper-api.service drugpaper-workbench.service drugpaper-web.service
systemctl --user status drugpaper-api.service drugpaper-workbench.service drugpaper-web.service
journalctl --user -u drugpaper-web.service -u drugpaper-workbench.service -n 100 --no-pager
```

常驻服务运行时不要同时执行 `npm run dev:offline`，避免端口冲突。前端沿用现有 Vite 开发工作台；更新源码后由 Vite 加载，后端源码更新后需重启相应 API 服务。

```bash
systemctl --user status drugpaper-api.service drugpaper-detector.service
systemctl --user restart drugpaper-detector.service
systemctl --user restart drugpaper-api.service
journalctl --user -u drugpaper-api.service -n 100 --no-pager
journalctl --user -u drugpaper-detector.service -n 100 --no-pager
curl --noproxy '*' http://127.0.0.1:18000/healthz
```

检测 Worker 保持单进程；增加 API 进程不增加 GPU 模型实例。更新代码/模型后重启相关服务，权重替换不能直接依赖旧缓存。队列、每用户配额、模型参数及原始坐标规则见 `docs/interfaces/molecule-detection-tool.md`。

## 重建与配置

```bash
# 普通应用依赖，勿把 GPU 推理包混装到 API 环境。
uv sync
bash deploy/install_yolo_gpu.sh

# 保留已有令牌，生成本机单位文件与 remote 配置。
.venv/bin/python deploy/prepare_runtime.py --device 0 --worker-python /home/xq/source/agent_source/.venv-yolo/bin/python --cuda-visible-devices 0

# 已安装的用户服务是指向项目 deploy/systemd 下文件的软链接。
systemctl --user daemon-reload
systemctl --user restart drugpaper-detector.service drugpaper-api.service
```

首次安装到其他机器需要更改实际权重/项目位置，在 `~/.config/systemd/user/` 安装相应 unit 软链接，并执行 `systemctl --user enable --now`；跨机器的用户 Linger 设置需按运行环境配置。现有基础设施管理见 `/home/xq/projects/drugpaper-infra/README.md`，不要运行工程根目录的开发 Compose 去创建另一套数据库。

## 验收与使用

```bash
.venv/bin/python deploy/verify_runtime.py
.venv/bin/python -m drugpaper.cli ask --doc doc_01M3W1D8FXH2141FHVS02HZF78 'UCM-1306 对应论文中的哪个化合物编号？'
```

验收脚本会执行真实检测和文本模型调用；离线样例论文只导入一次，重复运行去重。检测用两个用户并发测试缓存；实际文档节点经 Tool → 共享 Worker → GPU 完整调用。当前报告：`samples/outputs/deployment/20261001-233020/report.md`，视觉模型记录 vlm.json，问答记录 qa.json。

已验证 PostgreSQL 迁移/扩展、Redis、MinIO readiness、MinerU 接口连通、MolParser 健康、能力目录、GPU 检测、多用户缓存、文本模型、VLM 及真实文档问答。GPT-6 Luna 在约 13 秒内回答 UCM-1306 对应化合物 26，并引用原文。首次 checkpoint 初始化已修复为先创建并发索引、再开启业务事务，避免新部署首次问答阻塞。

GPU 服务已实际分配显存约 449 MiB，未占用原有 GPU 1 任务。MinerU 本次做连通性验证，样例导入使用已有离线解析，不重复提交整篇在线解析。


## P4 升级验收（2026-10-02）

业务库迁移前备份：`/home/xq/projects/drugpaper-infra/backups/postgres/drugpaper-20261001T164648Z-4056140.dump`。迁移升级至0006，新建七个化学事实表；API 与共享 GPU Worker 已重启加载0.4.0。

```bash
.venv/bin/python -m drugpaper.cli molecules --doc <已有文档ID>
.venv/bin/python -m scripts.verify_phase_4
```

真实验收采用隔离 SQLite 库及用户资产，调用当前共享 GPU Worker 与外部 MolParser，未向已有业务论文写入测试事实。报告位于 `samples/outputs/phase-4/20261002-final/report.json`；17个视觉节点、54个识别候选、化合物3/26的 InChIKey 对比通过。独立 PostgreSQL 集成回归验证迁移与正式事实投影。Docker CPU/GPU target 已提供，当前部署验收针对 systemd GPU Worker，未运行 Docker 构建。

## P5 图像多 Agent Worker（2026-10-02）

已备份业务库至 `/home/xq/projects/drugpaper-infra/backups/postgres/drugpaper-20261002T044504Z-854565.dump` 并升级0007。新增 `drugpaper-worker.service`，消费 batch/interactive 两个队列，进程并发2、prefetch=1，内部每文档图像并发6，共享原有 GPU 检测服务。MinerU 使用已确认的8002端口。

```bash
systemctl --user status drugpaper-worker.service
systemctl --user restart drugpaper-api.service drugpaper-worker.service
journalctl --user -u drugpaper-worker.service -n 100 --no-pager
.venv/bin/python -m drugpaper.cli figures --doc <文档ID>
.venv/bin/python -m scripts.verify_phase_5
```

真实10篇验收在隔离 PostgreSQL `drugpaper_p5_verify_20261002` 和 `samples/outputs/phase-5/20261002/` 资产中执行。模型端点全局 Redis 租约并发8；图表结构化成功但存在未确认字段时返回 needs_review。Celery 硬终止恢复测试使用独立测试队列、FakeVLM 和 PostgreSQL，避免影响业务任务。

Compose agents profile 是未构建验收的部署模板；容器内的127.0.0.1指向容器自身，外部 MinerU/MolParser/YOLO 地址必须配置为容器可达地址。当前正式部署方式为 systemd。
