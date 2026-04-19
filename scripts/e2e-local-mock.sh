#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-artifacts/mock}"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

if [ "${SOLRL_IN_DOCKER:-}" != "1" ]; then
  echo "e2e-local-mock.sh must run inside Docker. Use: docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh" >&2
  exit 1
fi

python3 -m solrl_core.cli local-mock \
  --config solrl.toml \
  --work-dir "$WORK_DIR" \
  --state "$WORK_DIR/hook_state.json" \
  --nonce local-attempt-1

cargo test -p solrl-registry --test registry_flow

echo "SolRL local mock e2e OK"
echo "Artifacts: $WORK_DIR"
