FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir boto3 cbor2 cryptography pytest requests ruff

COPY docker/nix-builder-entrypoint.sh /usr/local/bin/aws-nitro-runner-entrypoint.sh
RUN chmod +x /usr/local/bin/aws-nitro-runner-entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/aws-nitro-runner-entrypoint.sh"]
CMD ["./scripts/e2e-aws-nitro.sh"]
