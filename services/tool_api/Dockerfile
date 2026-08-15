FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/tool_api/requirements.txt .
RUN pip install --no-cache-dir \
    --default-timeout=120 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

COPY services/tool_api/app/ ./app/
COPY observability/ ./observability/
COPY agent/ ./agent/
COPY tools/ ./tools/

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
