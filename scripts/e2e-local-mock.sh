#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-artifacts/mock}"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

if [ "${SOLRL_IN_DOCKER:-}" != "1" ]; then
  echo "e2e-local-mock.sh must run inside Docker. Use: docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh" >&2
  exit 1
fi

python3 -m solrl_core.mock_worker --config solrl.toml --out "$WORK_DIR" --nonce local-attempt-1
python3 -m solrl_core.mock_verifier --config solrl.toml --work-dir "$WORK_DIR"
python3 -m solrl_core.mock_hook --config solrl.toml --work-dir "$WORK_DIR" --state "$WORK_DIR/hook_state.json"

if python3 -m solrl_core.mock_hook --config solrl.toml --work-dir "$WORK_DIR" --state "$WORK_DIR/hook_state.json" 2>"$WORK_DIR/replay.err"; then
  echo "expected replay rejection, but replay succeeded" >&2
  exit 1
fi

grep -q "replay detected" "$WORK_DIR/replay.err"

cargo test -p solrl-registry --test registry_flow

echo "SolRL local mock e2e OK"
echo "Artifacts: $WORK_DIR"
