#!/usr/bin/env bash
set -euo pipefail

docker --version
docker compose version
docker compose config >/dev/null
echo "Docker Compose config OK"

