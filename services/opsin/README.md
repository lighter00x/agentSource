# 可选独立 OPSIN 服务

主 API 和 Celery Worker 只调用 HTTP。这里是独立 FastAPI 进程，通过 JPype 加载官方 OPSIN jar；每进程一个 JVM，两个并发解析位，512MB JVM 堆。

从仓库根运行 `docker compose --profile chemistry up --build opsin`，再显式设置调用方 `OPSIN_BASE_URL=http://127.0.0.1:8088`、`OPSIN_MODEL_VERSION=2.8.0`。容器内调用可使用 `http://opsin:8080`。没有配置时名称校验跳过并保留复核状态。

本地运行也可安装 requirements.txt，设置 `OPSIN_JAR=/absolute/path/opsin-core-2.8.0-jar-with-dependencies.jar`，执行 `uvicorn services.opsin.server:app --host 127.0.0.1 --port 8088 --workers 1`。可用 `OPSIN_JAR_SHA256` 校验固定下载版本；生产部署应固定镜像及 jar 哈希。

GET `/ethanol.json` 返回 status/smiles/message/warnings；失败为 HTTP404 + FAILURE JSON，不把 WARNING 当作认证成功。服务不修正 OCR 名称、不忽略未能解释的立体化学。GET `/healthz` 用于就绪检查。

依赖与方法来源：[官方 OPSIN 2.8.0 发布](https://github.com/dan2097/opsin/releases/tag/2.8.0)、[官方 OpsinResult](https://github.com/dan2097/opsin/blob/master/opsin-core/src/main/java/uk/ac/cam/ch/wwmm/opsin/OpsinResult.java)。本服务是可部署交付件，不表示已替换正在运行的业务服务。
