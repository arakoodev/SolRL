#!/usr/bin/env bash
set -euo pipefail

if [ "${SOLRL_IN_DOCKER:-}" != "1" ]; then
  echo "e2e-aws-nitro.sh must run inside Docker." >&2
  echo "Use: docker compose run --rm aws-nitro-runner" >&2
  exit 1
fi

python3 -m solrl_core.aws_nitro_runner smoke "$@"
