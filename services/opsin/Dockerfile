FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/opsin/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
ARG OPSIN_VERSION=2.8.0
ADD https://github.com/dan2097/opsin/releases/download/${OPSIN_VERSION}/opsin-core-${OPSIN_VERSION}-jar-with-dependencies.jar /app/opsin.jar
RUN echo "8fd3f6ea032adeee37caa69868e94373e3595691a13e93a6d3ed6355930d7f84  /app/opsin.jar" | sha256sum -c -
COPY services/opsin/server.py /app/server.py
ENV OPSIN_JAR=/app/opsin.jar OPSIN_VERSION=${OPSIN_VERSION}
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
