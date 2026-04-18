#!/usr/bin/env bash
set -euo pipefail
exec > >(tee /var/log/solrl-nitro-smoke.log /dev/console) 2>&1
on_err() {
  rc=$?
  echo SOLRL_REMOTE_FAILED=$rc
  systemctl status nitro-enclaves-allocator.service --no-pager -l || true
  journalctl -u nitro-enclaves-allocator.service --no-pager -n 120 || true
  tail -160 /tmp/solrl-docker-build.log 2>/dev/null || true
  exit $rc
}
trap on_err ERR

yum update -y >/dev/null
amazon-linux-extras install aws-nitro-enclaves-cli -y >/dev/null
yum install -y aws-nitro-enclaves-cli-devel docker jq python3 >/dev/null
systemctl enable --now docker

install -d -m 0755 /etc/nitro_enclaves
cat >/etc/nitro_enclaves/allocator.yaml <<'YAML'
---
memory_mib: 1024
cpu_count: 2
YAML
systemctl enable nitro-enclaves-allocator.service >/dev/null
systemctl daemon-reload
systemctl restart nitro-enclaves-allocator.service
export NITRO_CLI_ARTIFACTS=/var/lib/solrl/nitro-artifacts
export NITRO_CLI_BLOBS=/usr/share/nitro_enclaves/blobs
mkdir -p "$NITRO_CLI_ARTIFACTS"
test -d "$NITRO_CLI_BLOBS"

WORK=/opt/solrl-nitro-worker
rm -rf "$WORK"
mkdir -p "$WORK/src"
base64 -d > "$WORK/Cargo.toml" <<'B64'
__CARGO_TOML_B64__
B64
base64 -d > "$WORK/src/main.rs" <<'B64'
__MAIN_RS_B64__
B64
base64 -d > "$WORK/Dockerfile" <<'B64'
__DOCKERFILE_B64__
B64

docker build "$WORK" \
  --build-arg SOLRL_USER_DATA_HEX=__USER_DATA_HEX__ \
  --build-arg SOLRL_PUBLIC_KEY_HEX=__PUBLIC_KEY_HEX__ \
  --build-arg SOLRL_NONCE_HEX=__NONCE_HEX__ \
  --build-arg SOLRL_VSOCK_PORT=__VSOCK_PORT__ \
  -t solrl-nitro-worker:__RUN_ID__ >/tmp/solrl-docker-build.log 2>&1

nitro-cli build-enclave --docker-uri solrl-nitro-worker:__RUN_ID__ --output-file /tmp/solrl-worker.eif \
  >/tmp/solrl-build.json
nitro-cli run-enclave --cpu-count 2 --memory 512 --enclave-cid 16 --eif-path /tmp/solrl-worker.eif \
  >/tmp/solrl-run.json
sleep 5
python3 - <<'PY' > /tmp/solrl-attestation.hex
import socket
import time

sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
for _ in range(90):
    try:
        sock.connect((16, __VSOCK_PORT__))
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("could not connect to SolRL worker enclave over VSOCK")

chunks = []
while True:
    data = sock.recv(65536)
    if not data:
        break
    chunks.append(data)
print(b"".join(chunks).decode("ascii").strip())
PY
nitro-cli describe-enclaves >/tmp/solrl-describe.json
ENCLAVE_ID="$(jq -r '.[0].EnclaveID // empty' /tmp/solrl-describe.json)"
test -n "$ENCLAVE_ID"
nitro-cli terminate-enclave --enclave-id "$ENCLAVE_ID" >/tmp/solrl-terminate.json

echo SOLRL_EXPECTED_USER_DATA_HEX=__USER_DATA_HEX__
echo SOLRL_EXPECTED_PUBLIC_KEY_HEX=__PUBLIC_KEY_HEX__
echo SOLRL_BUILD_JSON_B64="$(base64 -w0 /tmp/solrl-build.json)"
sleep 3
echo SOLRL_ATTESTATION_HEX_BEGIN
ATTESTATION_CHUNK_WIDTH=96
ATTESTATION_CHUNKS="$(fold -w "$ATTESTATION_CHUNK_WIDTH" /tmp/solrl-attestation.hex | wc -l)"
echo SOLRL_ATTESTATION_HEX_WIDTH="$ATTESTATION_CHUNK_WIDTH"
echo SOLRL_ATTESTATION_HEX_CHUNKS="$ATTESTATION_CHUNKS"
for pass in 1 2; do
    echo SOLRL_ATTESTATION_HEX_PASS="$pass"
    fold -w "$ATTESTATION_CHUNK_WIDTH" /tmp/solrl-attestation.hex | awk '{ printf "SOLRL_ATTESTATION_HEX_CHUNK=%04d:%s\n", NR - 1, $0 }'
done
echo SOLRL_ATTESTATION_HEX_END
echo SOLRL_STATUS=OK
