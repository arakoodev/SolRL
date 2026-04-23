#!/usr/bin/env bash
set -euo pipefail

if [ "${SOLRL_IN_DOCKER:-}" != "1" ]; then
  echo "test-anchor.sh must run inside the SolRL Docker dev shell. Use: docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh" >&2
  exit 1
fi

rustup toolchain install 1.88.0 >/dev/null
rustup default 1.88.0 >/dev/null

cargo test --workspace --locked
anchor build --no-idl
