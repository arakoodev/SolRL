#!/usr/bin/env bash
set -euo pipefail

git config --global --add safe.directory /workspace 2>/dev/null || true
exec "$@"
