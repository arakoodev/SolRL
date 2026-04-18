#!/usr/bin/env bash
set -euo pipefail

export HOME=/root
LOG_DIR=/var/log/solrl
CONSOLE=/dev/console
SRC_DIR=/opt/solrl
RESULT_LINK=/opt/solrl-eif-result
PHASE=init

mkdir -p "$LOG_DIR"
exec >"$LOG_DIR/user-data.log" 2>&1

emit_failure() {
  rc=$?
  trap - ERR
  log_path="$LOG_DIR/${PHASE}.log"
  {
    echo SOLRL_RESULT_BEGIN
    echo SOLRL_STATUS=FAILED
    echo SOLRL_RUN_ID=__RUN_ID__
    echo SOLRL_GIT_REF=__GIT_REF__
    echo SOLRL_PHASE="$PHASE"
    echo SOLRL_ERROR_TAIL_BEGIN
    if [ -f "$log_path" ]; then
      tail -80 "$log_path" | sed 's/[^[:print:]	]//g' || true
    else
      tail -80 "$LOG_DIR/user-data.log" | sed 's/[^[:print:]	]//g' || true
    fi
    echo SOLRL_ERROR_TAIL_END
    echo SOLRL_RESULT_END
  } >"$CONSOLE" || true
  shutdown -h now || true
  exit "$rc"
}
trap emit_failure ERR

run_phase() {
  PHASE="$1"
  shift
  echo "=== SOLRL phase: $PHASE ==="
  "$@" >"$LOG_DIR/${PHASE}.log" 2>&1
}

phase_packages() {
  yum update -y
  amazon-linux-extras install aws-nitro-enclaves-cli -y
  yum install -y aws-nitro-enclaves-cli-devel curl git jq python3 python3-pip xz
  python3 -m pip install --upgrade pip
  python3 -m pip install 'boto3<1.34' 'botocore<1.34' 'cbor2<6' 'cryptography<42'
}

phase_nix() {
  if ! command -v nix >/dev/null 2>&1; then
    curl -fsSL https://nixos.org/nix/install -o /tmp/solrl-install-nix.sh
    sh /tmp/solrl-install-nix.sh --daemon --yes --no-channel-add
  fi
  mkdir -p /etc/nix
  cat >/etc/nix/nix.conf <<'NIXCONF'
experimental-features = nix-command flakes
accept-flake-config = true
sandbox = true
sandbox-fallback = false
NIXCONF
  systemctl restart nix-daemon.service
}

phase_clone() {
  git_url="$(printf '%s' '__GIT_URL_B64__' | base64 -d)"
  rm -rf "$SRC_DIR"
  git clone "$git_url" "$SRC_DIR"
  cd "$SRC_DIR"
  git checkout --detach "__GIT_REF__"
}

phase_build_eif() {
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
  cd "$SRC_DIR"
  rm -f "$RESULT_LINK"
  for attempt in 1 2 3; do
    if nix build .#solrl-nitro-worker-eif --no-write-lock-file --out-link "$RESULT_LINK"; then
      break
    fi
    if [ "$attempt" = 3 ]; then
      return 1
    fi
    sleep $((attempt * 20))
  done
  find -L "$RESULT_LINK" -type f -name '*.eif' -print -quit >/tmp/solrl-eif-path
  test -s /tmp/solrl-eif-path
  eif_path="$(cat /tmp/solrl-eif-path)"
  sha384sum "$eif_path" >/tmp/solrl-eif-sha384.txt
  nitro-cli describe-eif --eif-path "$eif_path" >/tmp/solrl-build.json
}

phase_allocator() {
  install -d -m 0755 /etc/nitro_enclaves
  cat >/etc/nitro_enclaves/allocator.yaml <<'YAML'
---
memory_mib: 1024
cpu_count: 2
YAML
  systemctl enable nitro-enclaves-allocator.service
  systemctl daemon-reload
  systemctl restart nitro-enclaves-allocator.service
}

phase_run_enclave() {
  eif_path="$(cat /tmp/solrl-eif-path)"
  nitro-cli run-enclave --cpu-count 2 --memory 512 --enclave-cid 16 --eif-path "$eif_path" \
    >/tmp/solrl-run.json
}

phase_attestation() {
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

payload = "\n".join([
    "USER_DATA_HEX=__USER_DATA_HEX__",
    "PUBLIC_KEY_HEX=__PUBLIC_KEY_HEX__",
    "NONCE_HEX=__NONCE_HEX__",
    "",
])
sock.sendall(payload.encode("ascii"))
sock.shutdown(socket.SHUT_WR)
chunks = []
while True:
    data = sock.recv(65536)
    if not data:
        break
    chunks.append(data)
print(b"".join(chunks).decode("ascii").strip())
PY
}

phase_verify_attestation() {
  PYTHONPATH="$SRC_DIR/python" python3 -m solrl_core.aws_nitro_runner verify-attestation \
    --attestation-hex-path /tmp/solrl-attestation.hex \
    --expected-user-data-hex __USER_DATA_HEX__ \
    --expected-public-key-hex __PUBLIC_KEY_HEX__ \
    --summary-json /tmp/solrl-attestation-summary.json
}

phase_terminate_enclave() {
  nitro-cli describe-enclaves >/tmp/solrl-describe.json
  enclave_id="$(jq -r '.[0].EnclaveID // empty' /tmp/solrl-describe.json)"
  test -n "$enclave_id"
  nitro-cli terminate-enclave --enclave-id "$enclave_id" >/tmp/solrl-terminate.json
}

run_phase packages phase_packages
run_phase nix phase_nix
run_phase clone phase_clone
run_phase build_eif phase_build_eif
run_phase allocator phase_allocator
run_phase run_enclave phase_run_enclave
run_phase attestation phase_attestation
run_phase verify_attestation phase_verify_attestation
run_phase terminate_enclave phase_terminate_enclave

summary=/tmp/solrl-attestation-summary.json
eif_sha384="$(awk '{ print $1 }' /tmp/solrl-eif-sha384.txt)"
pcr0="$(jq -r '.pcrs["0"]' "$summary")"
pcr1="$(jq -r '.pcrs["1"]' "$summary")"
pcr2="$(jq -r '.pcrs["2"]' "$summary")"
pcr16="$(jq -r '.pcrs["16"]' "$summary")"
root_sha="$(jq -r '.root_public_key_sha256' "$summary")"

{
  echo SOLRL_RESULT_BEGIN
  echo SOLRL_STATUS=OK
  echo SOLRL_RUN_ID=__RUN_ID__
  echo SOLRL_GIT_REF=__GIT_REF__
  echo SOLRL_EIF_SHA384="$eif_sha384"
  echo SOLRL_NITRO_ROOT_SHA256="$root_sha"
  echo SOLRL_PCR0="$pcr0"
  echo SOLRL_PCR1="$pcr1"
  echo SOLRL_PCR2="$pcr2"
  echo SOLRL_PCR16="$pcr16"
  echo SOLRL_RESULT_END
} >"$CONSOLE"

shutdown -h now || true
