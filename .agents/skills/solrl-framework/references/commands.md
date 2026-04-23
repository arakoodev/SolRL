# SolRL Commands

Use Docker Compose for everything. No host package installs.

## Local Gates

```bash
docker compose build dev-shell harbor-runner aws-test-runner
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm --no-deps harbor-runner pytest -q
docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

The lint gate also checks Dependabot: root Cargo is direct-only, the Nitro worker has its own Cargo lane, vendored code is
excluded, and Docker base images do not use floating `latest` tags.

The local MVP path can also be called directly:

```bash
docker compose run --rm --no-deps harbor-runner \
  python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
```

## Anchor And Registry

```bash
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

This runs workspace Rust tests and `anchor build --no-idl`. `--no-idl` is currently intentional.

## Nix EIF Build

```bash
docker compose run --rm nix-builder nix --version
docker compose run --rm nix-builder nix build --no-link --print-out-paths \
  .#solrl-nitro-worker \
  .#solrl-nitro-kernel-bundle \
  .#solrl-nitro-worker-root \
  .#solrl-nitro-worker-eif
```

This checks the Marlin/Oyster-style Nix EIF path locally in a container. The staged commands mirror GitHub Actions cache
boundaries: static worker, Marlin/Oyster kernel bundle, app root, final EIF. Locally, use one `nix-builder` container for
all four targets so the Nix store and Git inputs are reused during that run. The real AWS smoke pulls the CI-built EIF
from public GHCR instead of rebuilding it on EC2.

The shipped smoke path expects GitHub Actions to publish the raw EIF as a public GHCR OCI artifact. Test the workflow
shape locally with:

```bash
act pull_request -W .github/workflows/build-nitro-eif.yml -j build-nitro-eif
```

The EC2 parent then pulls that `.eif` with ORAS and verifies its `.sha384` sidecar before booting it.

## LocalStack

```bash
docker compose up -d localstack
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

LocalStack validates AWS API and Terraform behavior. It cannot emulate `/dev/nsm`, PCRs, EIF boot, VSOCK, or Nitro attestation.

## Verifier Service

```bash
docker compose up verifier-service
curl -fsS http://localhost:8787/healthz
```

The service is the mock long-running verifier API:

```text
GET  /healthz
POST /verify/mock
```

Do not use this as evidence of real Nitro. It signs only after mock attestation checks pass.

## Harbor Import Path

Use this import path when asking Harbor to instantiate SolRL's environment surface:

```text
solrl_harbor.nitro_environment:NitroEnvironment
```

Local mode uses a deterministic workspace transport. AWS mode intentionally errors until the production VSOCK worker RPC is wired.

## Real AWS Nitro

Put credentials in `.env`:

```text
AWS_ACCESS_KEY=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

Then:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

Use cleanup only for a known run id:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner cleanup --run-id solrl-YYYYMMDDHHMMSS-xxxxxxxx
```

The cleanup code must refuse to touch resources unless both tags match:

```text
Project=SolRL
SolRLRunId=<exact run id>
```

## Focused Test Commands

```bash
docker compose run --rm --no-deps harbor-runner pytest -q tests/test_claim_flow.py
docker compose run --rm --no-deps harbor-runner pytest -q tests/test_aws_nitro_runner.py
docker compose run --rm --no-deps harbor-runner pytest -q tests/test_verifier_service.py tests/test_harbor_nitro_environment.py tests/test_cli.py
docker compose run --rm --no-deps dev-shell cargo test --workspace
```

## Verification Commands

Run the local token proof:

```bash
docker compose run --rm --no-deps harbor-runner \
  python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
```

Inspect it:

```bash
docker compose run --rm --no-deps -T harbor-runner python - <<'PY'
import json
from pathlib import Path

root = Path("artifacts/mock")
result = json.loads((root / "mvp_result.json").read_text())
receipt = json.loads((root / "claim_receipt.json").read_text())
state = json.loads((root / "hook_state.json").read_text())
assert result["status"] == "paid"
assert result["replay_rejected"] is True
assert len(state["ledger"]) == 1
assert state["ledger"][0]["claim_hash"] == receipt["claim_hash"]
print("LOCAL_TOKEN_PROOF_OK")
PY
```

Run the on-chain implementation proof:

```bash
docker compose run --rm lint
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

Run the real Nitro proof:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

Inspect it:

```bash
RUN_ID=<run-id>
docker compose run --rm --no-deps -T harbor-runner python - <<PY
import json
from pathlib import Path

run_id = "$RUN_ID"
root = Path("artifacts/aws-nitro") / run_id
markers = json.loads((root / "remote-markers.json").read_text())
postaudit = json.loads((root / "postaudit-project.json").read_text())
assert markers["SOLRL_STATUS"] == "OK"
assert markers["SOLRL_PCR16"] == markers["SOLRL_CLAIM_PCR16"]
assert len(postaudit["instances"]) == 0
assert len(postaudit["security_groups"]) == 0
assert len(postaudit["volumes"]) == 0
print("REAL_NITRO_PROOF_OK")
PY
```
