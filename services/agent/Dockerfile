FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY drugpaper ./drugpaper
COPY config ./config
COPY prompts ./prompts
RUN pip install --no-cache-dir .
CMD ["celery", "-A", "drugpaper.workers.celery_app:app", "worker", "--queues=batch,interactive", "--concurrency=2", "--loglevel=INFO"]
