#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor"
mkdir -p "$VENDOR"

clone_at_ref() {
  local name="$1"
  local url="$2"
  local ref="$3"
  local dest="$VENDOR/$name"
  if [ ! -d "$dest/.git" ]; then
    git clone --filter=blob:none --no-checkout "$url" "$dest"
  fi
  git -C "$dest" fetch --depth 1 origin "$ref" || git -C "$dest" fetch --all --tags
  git -C "$dest" checkout --detach "$ref"
}

clone_at_ref oyster-monorepo https://github.com/marlinprotocol/oyster-monorepo.git f60874a27f56eee2974cf0099ff80e9627c8f1dd
clone_at_ref oyster-solana-contracts https://github.com/marlinprotocol/oyster-solana-contracts.git 73eb72337cfe4985b4cc532b0f45287d9982a5b8
clone_at_ref harbor https://github.com/harbor-framework/harbor.git 3396e6f1f82b831108d26b0273d24f5424519f86

echo "Pinned baselines cloned under $VENDOR"
