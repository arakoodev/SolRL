#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'MSG'
Real AWS Nitro smoke is not runnable on a normal laptop or in LocalStack.

Required runtime:
- AWS Nitro-capable EC2 instance with Nitro Enclaves enabled
- nitro-cli available on the parent instance
- built worker EIF and verifier EIF
- parent/enclave VSOCK proxy setup

LocalStack can test AWS APIs and artifact storage, but it cannot emulate /dev/nsm,
PCRs, EIF boot, or real Nitro attestations.
MSG

if ! command -v nitro-cli >/dev/null 2>&1; then
  exit 2
fi

echo "nitro-cli found. Real Nitro smoke implementation should run here."
exit 1

