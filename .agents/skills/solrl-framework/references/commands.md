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

## Anchor And Registry

```bash
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

This runs workspace Rust tests and `anchor build --no-idl`. `--no-idl` is currently intentional.

## Nix EIF Build

```bash
docker compose run --rm nix-builder nix --version
docker compose run --rm nix-builder nix build --no-link --print-out-paths .#solrl-nitro-worker-eif
```

This checks the Marlin/Oyster-style Nix EIF path locally in a container. The real AWS smoke rebuilds the EIF on EC2.

## LocalStack

```bash
docker compose up -d localstack
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

LocalStack validates AWS API and Terraform behavior. It cannot emulate `/dev/nsm`, PCRs, EIF boot, VSOCK, or Nitro attestation.

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
docker compose run --rm --no-deps dev-shell cargo test --workspace
```
