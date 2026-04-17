FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir boto3 cbor2 cryptography pytest requests ruff

WORKDIR /workspace

CMD ["pytest", "-q"]
