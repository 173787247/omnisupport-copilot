FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY services/rag_api/requirements.txt /tmp/rag_api_requirements.txt
COPY services/tool_api/requirements.txt /tmp/tool_api_requirements.txt
COPY contracts ./contracts
COPY data ./data
COPY analytics ./analytics
COPY pipelines ./pipelines
COPY services ./services
COPY observability ./observability
COPY agent ./agent
COPY tools ./tools
COPY evals ./evals
COPY release ./release
COPY rollout ./rollout
COPY governance ./governance
COPY scripts ./scripts
COPY tests ./tests

RUN pip install --no-cache-dir \
    --default-timeout=120 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r /tmp/rag_api_requirements.txt \
    -r /tmp/tool_api_requirements.txt \
    -e ".[dev]"

CMD ["sh"]
