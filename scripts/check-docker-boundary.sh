#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if grep -RInE 'docker:dind|privileged:[[:space:]]*true|/var/run/docker\\.sock' \
  docker-compose.yml docker 2>/dev/null; then
  echo "docker boundary lint failed: default Docker-in-Docker or host socket access detected" >&2
  exit 1
fi

if grep -nE 'docker\\.io|docker-ce|docker-cli|docker-buildx|docker compose|docker-compose-plugin' \
  docker/dev-shell.Dockerfile 2>/dev/null; then
  echo "docker boundary lint failed: dev-shell must not install Docker CLI or Compose" >&2
  exit 1
fi

python3 - <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

bad_subjects = ("nitro", "nsm", "pcr", "eif", "vsock", "enclave")
negative_words = ("cannot", "can't", "does not", "doesn't", "not", "no ", "without")
paths = [Path("README.md"), Path("PLAN.md")]

for path in paths:
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        lower = line.lower()
        if "localstack" not in lower:
            continue
        if not any(subject in lower for subject in bad_subjects):
            continue
        if any(word in lower for word in negative_words):
            continue
        if re.search(r"localstack.*(emulates|emulate|simulates|simulate|supports|tests).*(nitro|nsm|pcr|eif|vsock|enclave)", lower):
            print(
                f"docker boundary lint failed: LocalStack overclaim at {path}:{lineno}: {line}",
                file=sys.stderr,
            )
            sys.exit(1)

print("docker boundary lint OK")
PY
