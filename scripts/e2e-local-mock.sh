#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${1:-artifacts/mock}"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

python -m solrl_core.mock_worker --config solrl.toml --out "$WORK_DIR" --nonce local-attempt-1
python -m solrl_core.mock_verifier --config solrl.toml --work-dir "$WORK_DIR"
python -m solrl_core.mock_hook --config solrl.toml --work-dir "$WORK_DIR" --state "$WORK_DIR/hook_state.json"

if python -m solrl_core.mock_hook --config solrl.toml --work-dir "$WORK_DIR" --state "$WORK_DIR/hook_state.json" 2>"$WORK_DIR/replay.err"; then
  echo "expected replay rejection, but replay succeeded" >&2
  exit 1
fi

grep -q "replay detected" "$WORK_DIR/replay.err"

echo "SolRL local mock e2e OK"
echo "Artifacts: $WORK_DIR"

