---
name: solrl-framework
description: Operate and modify the SolRL repository safely. Use when working on SolRL Docker workflows, generic compute proofs, Harbor mock evals, Anchor/Token-2022 registry code, verification proof runs, ClaimV1 schema parity, PCR16 hashing, LocalStack tests, real AWS Nitro smoke tests, Marlin/Oyster Nix EIF builds, AWS tagging and cleanup, or docs for this framework.
---

# SolRL Framework

SolRL is a Docker-first Harbor evaluation protocol scaffold with Solana Token-2022 settlement and real AWS Nitro attestation smoke tests.

Use this skill to keep another AI from improvising around the sharp edges. The boring path is the product here.

## First Read

1. Read `README.md` for the current public workflow.
2. Read `PLAN.md` when changing architecture, AWS, token settlement, PCR16, or test strategy.
3. If the user asks for release readiness, verification, or proof that it works, read the README section `Verification`.
4. Read the focused reference only when needed:
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
- Do not let Nitro `user_data` drift from registry PCR16 semantics. `pcr16_user_data` is the NSM ExtendPCR input;
  `pcr16_digest` is the locked PCR16 ClaimV1 signs and the registry recomputes.
- Do not remove `.github/dependabot.yml` or let root Cargo Dependabot manage indirect dependencies. Solana's lockfile has duplicate transitive crates and unconfigured Dependabot creates noisy failed update runs.

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
docker compose run --rm nix-builder nix build --no-link --print-out-paths \
  .#solrl-nitro-worker \
  .#solrl-nitro-kernel-bundle \
  .#solrl-nitro-worker-root \
  .#solrl-nitro-worker-eif
```

Run all local Nix EIF targets in one `nix-builder` container unless debugging one exact stage. Separate Compose
invocations rehydrate Git inputs and Nix store paths on cold caches. Slow, noisy, and easy to misdiagnose.

Use this for GitHub Actions EIF workflow checks through `act`:

```bash
act pull_request -W .github/workflows/build-nitro-eif.yml -j build-nitro-eif
```

Use this for real AWS only after local gates pass:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

The real AWS runner refuses the default path from a dirty worktree. Commit and push first so the EC2 source checkout and
the GHCR EIF artifact are the same commit.

## Verification Workflow

When the user asks to verify the system, do not only run the smoke. Produce evidence for three separate claims:

```text
1. Token settlement semantics: local-mock paid once and rejected replay.
2. On-chain token implementation: lint + Anchor tests prove Token-2022 CPI wiring, registry checks, and local-validator balance movement.
3. Real Nitro attestation: AWS smoke proves generic compute output, NSM attestation, AWS root verification, PCR16 bridge, ClaimV1 receipt, and cleanup.
```

Use:

```bash
docker compose build dev-shell harbor-runner aws-test-runner
docker compose run --rm lint
docker compose run --rm --no-deps harbor-runner pytest -q
docker compose run --rm --no-deps harbor-runner python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

Report these exact artifacts:

- `artifacts/mock/mvp_result.json`: `status=paid`, `replay_rejected=true`.
- `artifacts/mock/hook_state.json`: one ledger entry with amount, claim hash, job account, and payout token account.
- `artifacts/mock/claim_receipt.json`: ClaimV1, verifier signature, token mint, payout account, amount.
- `programs/solrl-registry/tests/registry_flow.rs`: `settle_claim_transfers_token2022_balance_with_registry_pda_authority` proves real Token-2022 balance movement.
- `artifacts/aws-nitro/<run-id>/remote-markers.json`: `SOLRL_STATUS=OK`, `SOLRL_PCR16 == SOLRL_CLAIM_PCR16`,
  `SOLRL_COMPUTE_OUTPUT_HASH`, and `SOLRL_ATTESTATION_DOCUMENT_HASH`.
- `artifacts/aws-nitro/<run-id>/nitro-claim-receipt.json`: ClaimV1 signed over the verified Nitro attestation document hash,
  generic compute output hash as `trajectory_hash`, and the locked PCR16.
- `artifacts/aws-nitro/<run-id>/run-instances.json`: enclave enabled, IMDSv2 required, exact SolRL tags.
- `artifacts/aws-nitro/<run-id>/postaudit-project.json`: zero SolRL instances, security groups, and volumes.
- `artifacts/aws-nitro/<run-id>/submission-proof-bundle.tar.gz`: self-contained reviewer artifact containing
  `submission-proof.json`, `MANIFEST.sha256`, console output, AWS launch traces, ClaimV1 receipt, audits, `run.log`, and
  `user-data.sh`.

Be precise about the token boundary. V1 has real Token-2022 CPI code in `settle_claim` and `slash_operator`, lints that
reject fake flag-only settlement, a local payout/replay simulator, and a local-validator Token-2022 balance test. Do not
claim the same-program transfer hook fires during settlement. Solana rejects `registry -> Token-2022 -> registry hook`
reentry; V1 settlement uses registry PDA authorities over escrow and stake vaults.

## Edit Rules

When touching AWS Nitro:

1. Edit `python/solrl_core/aws_nitro_runner.py` or templates under `python/solrl_core/aws_nitro_templates/`.
2. Keep all launch, tag, audit, cleanup, and result parsing logic in the main runner path.
3. Update `scripts/check-aws-safety.py` when a safety invariant should never regress.
4. Add or update `tests/test_aws_nitro_runner.py`.
5. Run `docker compose run --rm lint` and focused pytest.
6. Keep submission proof generation in the main runner path. Do not add sidecar proof scripts or JSON that points at files
   outside `submission-proof-bundle.tar.gz`.

When touching ClaimV1, PCR16, settlement, or slashing:

1. Update `crates/solrl-claim`, `programs/solrl-registry`, and `python/solrl_core/claim.py` together.
2. Update `tests/fixtures/claim_v1_golden.json` if the canonical bytes intentionally change.
3. Keep signed fields verified against on-chain state. A signed field that is never checked is a replay or forgery bug waiting for a Friday afternoon.
4. Run schema and registry lints through `docker compose run --rm lint`.

When touching the MVP command surface, verifier API, or Harbor import path:

1. Keep the local verification path on `python -m solrl_core.cli local-mock`.
2. Keep the verifier API on `python -m solrl_core.verifier_service`.
3. Keep the Harbor import path `solrl_harbor.nitro_environment:NitroEnvironment` documented.
4. Update `scripts/check-mvp-entrypoints.py` if the shape intentionally changes.
5. Run `docker compose run --rm --no-deps harbor-runner pytest -q tests/test_cli.py tests/test_verifier_service.py tests/test_harbor_nitro_environment.py`.

When touching Docker:

1. Keep `SOLRL_IN_DOCKER=1` for services that must refuse host execution.
2. Keep LocalStack dummy credentials isolated from `aws-nitro-runner`.
3. Keep Docker base image tags versioned, not `latest`, so Dependabot can produce real update PRs.
4. Run `scripts/check-docker-boundary.sh` and `scripts/check-dependabot-config.py` through the lint service.

## Real AWS Decision Point

The current no-S3, no-IAM smoke path uses EC2 console output as the return channel after cloud-init stops the instance. This is intentionally limited.

The real AWS path pulls a public GHCR OCI artifact for the raw EIF, verifies its `.sha384` sidecar on the EC2 parent, then boots it. If real AWS runs reach `pull_eif` but no final `SOLRL_RESULT_BEGIN` block appears, do not keep adding console tail hacks. That already failed. Read `references/troubleshooting.md`, then propose a real return channel as an explicit architecture decision.

The smoke must print both `SOLRL_PCR16` and `SOLRL_CLAIM_PCR16`, and they must match. If they do not, the AWS attestation
rail and the Solana settlement rail are proving different things.

## Completion Checklist

Before saying work is done:

1. Show `git status --short`.
2. State which Docker commands passed.
3. If this was a verification run, state the local token proof, Anchor/Token-2022 proof, and Nitro proof
   separately.
4. If real AWS ran, state the AWS account, run id, created resource tags, cleanup result, post-audit count, and
   `submission-proof-bundle.tar.gz` path plus SHA256.
5. If a command could not be run, say why. No pretend green checkmarks.
