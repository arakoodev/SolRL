#!/usr/bin/env bash
set -euo pipefail

rustup toolchain install 1.85.1 >/dev/null
rustup default 1.85.1 >/dev/null

cargo test --workspace
anchor build --no-idl
