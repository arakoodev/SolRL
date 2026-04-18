#!/usr/bin/env bash
set -euo pipefail

if [ "${SOLRL_IN_DOCKER:-}" != "1" ]; then
  echo "lint.sh must run inside the SolRL Docker dev shell. Use: docker compose run --rm --no-deps lint" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rustup toolchain install 1.88.0 >/dev/null
rustup default 1.88.0 >/dev/null
rustup component add rustfmt clippy >/dev/null

cargo fmt --all -- --check
cargo fmt --manifest-path crates/solrl-nitro-worker/Cargo.toml -- --check
cargo clippy --workspace --all-targets -- \
  -D clippy::all \
  -D clippy::unwrap_used \
  -D clippy::expect_used \
  -D clippy::panic \
  -D clippy::todo
cargo clippy --manifest-path crates/solrl-nitro-worker/Cargo.toml --all-targets -- \
  -D clippy::all \
  -D clippy::unwrap_used \
  -D clippy::expect_used \
  -D clippy::panic \
  -D clippy::todo

ruff check --line-length 120 python tests \
  scripts/check-aws-safety.py \
  scripts/check-claim-schema-parity.py \
  scripts/check-registry-claim-checks.py \
  scripts/check-token2022-wiring.py

./scripts/check-aws-safety.py
./scripts/check-claim-schema-parity.py
./scripts/check-registry-claim-checks.py
./scripts/check-token2022-wiring.py
./scripts/check-docker-boundary.sh

terraform -chdir=infra/localstack init -backend=false
terraform -chdir=infra/localstack fmt -recursive -check
terraform -chdir=infra/localstack validate
