FROM python:3.12-slim AS base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY drugpaper ./drugpaper
RUN pip install --no-cache-dir .
FROM base AS cpu
RUN pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir ultralytics==8.4.171
CMD ["uvicorn", "drugpaper.detection.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
FROM base AS gpu
RUN pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
RUN pip install --no-cache-dir ultralytics==8.4.171
CMD ["uvicorn", "drugpaper.detection.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
