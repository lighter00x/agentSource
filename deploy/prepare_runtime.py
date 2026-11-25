"""生成本机部署配置，不输出密钥，保留已配置的调用参数。"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import shutil
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--worker-python", type=Path)
    parser.add_argument("--cuda-visible-devices")
    args = parser.parse_args()
    env_file = ROOT / ".env"
    values = dotenv_values(env_file)
    source = Path(str(values.get("YOLO_MODEL_PATH") or ""))
    if not source.is_file():
        raise SystemExit("YOLO_MODEL_PATH 无效，停止部署")
    destination = ROOT / "data/models" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise SystemExit("部署目录已有不同权重，停止覆盖")
        shutil.copy2(source, destination)
    token = values.get("YOLO_SERVICE_TOKEN") or secrets.token_hex(32)
    admin = values.get("SKILLS_ADMIN_TOKEN") or secrets.token_hex(32)
    updates = {
        "YOLO_MODEL_PATH": str(destination),
        "YOLO_BACKEND": "remote",
        "YOLO_BASE_URL": "http://127.0.0.1:8010",
        "YOLO_SERVICE_TOKEN": token,
        "SKILLS_ADMIN_TOKEN": admin,
        "YOLO_DEVICE": args.device,
    }
    for name, value in updates.items():
        set_key(str(env_file), name, value)
    env_file.chmod(0o600)
    runtime = ROOT / "deploy/runtime.env"
    # EnvironmentFile 仅存无变量引用的部署项；业务 .env 由应用自己的 dotenv 解析。
    content = f"YOLO_SERVICE_TOKEN={token}\nSKILLS_ADMIN_TOKEN={admin}\nOMP_NUM_THREADS=2\nMKL_NUM_THREADS=2\nOPENBLAS_NUM_THREADS=2\nPYTHONUNBUFFERED=1\nYOLO_CONFIG_DIR={ROOT / 'data/yolo-config'}\n"
    runtime.write_text(content)
    runtime.chmod(0o600)
    (ROOT / "data/yolo-config").mkdir(parents=True, exist_ok=True)
    for name, target, port, workers in [
        ("detector", "drugpaper.detection.api:create_app", 8010, 1),
        ("api", "drugpaper.api.app:create_app", 18000, 2),
    ]:
        dependency = (
            "Wants=drugpaper-detector.service\nAfter=network-online.target drugpaper-detector.service"
            if name == "api"
            else "After=network-online.target"
        )
        override = (
            "Environment=YOLO_BACKEND=local\n" if name == "detector" else "Environment=YOLO_BACKEND=remote\n"
        )
        if name == "detector" and args.cuda_visible_devices:
            override += f"Environment=CUDA_VISIBLE_DEVICES={args.cuda_visible_devices}\n"
        interpreter = (
            args.worker_python if name == "detector" and args.worker_python else ROOT / ".venv/bin/python"
        )
        unit = f"""[Unit]
Description=Drugpaper {name}
{dependency}
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={ROOT}
EnvironmentFile={runtime}
{override}ExecStart={interpreter} -m uvicorn {target} --factory --host 127.0.0.1 --port {port} --workers {workers}
Restart=always
RestartSec=5
TimeoutStopSec=150
KillSignal=SIGTERM
UMask=0077

[Install]
WantedBy=default.target
"""
        (ROOT / "deploy/systemd" / f"drugpaper-{name}.service").write_text(unit)
    print("Prepared 2 service units; secrets remain in private configuration files.")
    print("weights_sha256=" + hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
