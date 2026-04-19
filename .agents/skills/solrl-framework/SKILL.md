---
name: solrl-framework
description: Operate and modify the SolRL repository safely. Use when working on SolRL Docker workflows, Harbor mock evals, Anchor/Token-2022 registry code, ClaimV1 schema parity, PCR16 hashing, LocalStack tests, real AWS Nitro smoke tests, Marlin/Oyster Nix EIF builds, AWS tagging and cleanup, or docs for this framework.
---

# SolRL Framework

SolRL is a Docker-first Harbor evaluation protocol scaffold with Solana Token-2022 settlement and real AWS Nitro attestation smoke tests.

Use this skill to keep another AI from improvising around the sharp edges. The boring path is the product here.

## First Read

1. Read `README.md` for the current public workflow.
2. Read `PLAN.md` when changing architecture, AWS, token settlement, PCR16, or test strategy.
3. Read the focused reference only when needed:
   - `references/commands.md` for exact Docker commands.
   - `references/architecture.md` for the end-to-end data flow.
   - `references/aws-nitro-safety.md` for real AWS rules.
   - `references/claim-registry.md` for ClaimV1, PCR16, and Token-2022 rules.
   - `references/troubleshooting.md` when a Docker, LocalStack, or Nitro run fails.

## Non-Negotiables

- Do not install Rust, Anchor, Solana CLI, Node, Python packages, Nix, Terraform, AWS CLI, or Docker tooling on the laptop. Use Docker Compose services.
- Do not commit `.env`, AWS credentials, `artifacts/`, caches, `target/`, `.terraform/`, or `vendor/`.
- Do not add privileged Docker-in-Docker or mount the host Docker socket unless the user explicitly accepts the trust cost.
- Do not claim LocalStack tests Nitro. LocalStack is for AWS API and Terraform workflow checks only.
- Do not create sidecar AWS smoke runners. Real AWS Nitro must flow through `python -m solrl_core.aws_nitro_runner` and `scripts/e2e-aws-nitro.sh`.
- Do not create, mutate, or delete AWS resources without exact SolRL tags and pre/post audits.
- Do not use broad cleanup. Delete only resources tagged `Project=SolRL` and the exact current `SolRLRunId`.
- Do not introduce S3, IAM roles, SSM, SSH keys, inbound security group rules, or debug-mode Nitro into the smoke path without naming it as an architecture change and getting user approval.
- Do not change ClaimV1, SlashClaimV1, or PCR16 on only one side. Rust and Python must stay byte-compatible.

## Standard Workflow

Use this flow for normal repo work:

```bash
docker compose build dev-shell harbor-runner aws-test-runner
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm --no-deps harbor-runner pytest -q
docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

Use this for Anchor/Solana code:

```bash
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

Use this for Nix EIF build checks without touching AWS:

```bash
docker compose run --rm nix-builder nix build --no-link --print-out-paths .#solrl-nitro-worker-eif
```

Use this for real AWS only after local gates pass:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

## Edit Rules

When touching AWS Nitro:

1. Edit `python/solrl_core/aws_nitro_runner.py` or templates under `python/solrl_core/aws_nitro_templates/`.
2. Keep all launch, tag, audit, cleanup, and result parsing logic in the main runner path.
3. Update `scripts/check-aws-safety.py` when a safety invariant should never regress.
4. Add or update `tests/test_aws_nitro_runner.py`.
5. Run `docker compose run --rm lint` and focused pytest.

When touching ClaimV1, PCR16, settlement, or slashing:

1. Update `crates/solrl-claim`, `programs/solrl-registry`, and `python/solrl_core/claim.py` together.
2. Update `tests/fixtures/claim_v1_golden.json` if the canonical bytes intentionally change.
3. Keep signed fields verified against on-chain state. A signed field that is never checked is a replay or forgery bug waiting for a Friday afternoon.
4. Run schema and registry lints through `docker compose run --rm lint`.

When touching Docker:

1. Keep `SOLRL_IN_DOCKER=1` for services that must refuse host execution.
2. Keep LocalStack dummy credentials isolated from `aws-nitro-runner`.
3. Run `scripts/check-docker-boundary.sh` through the lint service.

## Real AWS Decision Point

The current no-S3, no-IAM smoke path uses EC2 console output as the return channel after cloud-init stops the instance. This is intentionally limited.

If real AWS runs reach `build_eif` but no final `SOLRL_RESULT_BEGIN` block appears, do not keep adding console tail hacks. That already failed. Read `references/troubleshooting.md`, then propose a real return channel as an explicit architecture decision.

## Completion Checklist

Before saying work is done:

1. Show `git status --short`.
2. State which Docker commands passed.
3. If real AWS ran, state the AWS account, run id, created resource tags, cleanup result, and post-audit count.
4. If a command could not be run, say why. No pretend green checkmarks.
