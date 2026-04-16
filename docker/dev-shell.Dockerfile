FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=20
ARG ANCHOR_INSTALL_RUST_VERSION=1.79.0
ARG RUST_VERSION=1.88.0
ARG ANCHOR_VERSION=0.30.1
ARG SOLANA_VERSION=v1.18.26
ARG TERRAFORM_VERSION=1.6.6

ENV PATH="/root/.cargo/bin:/root/.local/share/solana/install/active_release/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git gnupg jq unzip xz-utils pkg-config \
      build-essential libssl-dev libudev-dev python3 python3-pip python3-venv \
      protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain ${ANCHOR_INSTALL_RUST_VERSION}

RUN cargo +${ANCHOR_INSTALL_RUST_VERSION} install --locked anchor-cli --version ${ANCHOR_VERSION}

RUN rustup toolchain install ${RUST_VERSION} \
    && rustup default ${RUST_VERSION}

RUN curl -fsSL https://release.anza.xyz/${SOLANA_VERSION}/install | bash

RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/terraform.zip \
    && unzip /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip

RUN pip3 install --no-cache-dir awscli awscli-local boto3 cryptography pytest requests ruff

WORKDIR /workspace

CMD ["bash"]
