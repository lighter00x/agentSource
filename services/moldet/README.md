# MolDet

共享实现位于 drugpaper/detection，保持单 Worker、用户轮转队列、专用推理线程。启动预热，GET /health；保留原 POST /detect 单图协议，新增 POST /detect/batch JSON 和 POST /detect/files multipart。两个批量协议都按 input_index 输出坏图占位和 confidence/cls。

从工程根目录构建：`docker build -f services/moldet/Dockerfile --target gpu -t drugpaper-moldet .`（CPU 使用 --target cpu）。挂载权重，配置 YOLO_MODEL_PATH、YOLO_DEVICE、YOLO_SERVICE_TOKEN；内部认证支持 Bearer 与 X-Service-Token。GPU 容器需要宿主机 GPU runtime；当前机器仍采用已部署的 systemd GPU Worker，不替换成容器。参数沿用 YOLO_CONF/YOLO_IOU/YOLO_IMGSZ，不能在并发请求中改写全局配置。
