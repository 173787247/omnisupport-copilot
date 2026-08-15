FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app:/workspace

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY services/rag_api/requirements.txt .
RUN pip install --no-cache-dir \
    --default-timeout=120 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# 源码
COPY services/rag_api/app/ ./app/
COPY services/graph/ /workspace/services/graph/
COPY pipelines/ /workspace/pipelines/
COPY observability/ ./observability/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
