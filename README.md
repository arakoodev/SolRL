# SolRL

SolRL is a Docker-first scaffold for hardware-attested compute backed by AWS Nitro attestations and Solana Token-2022 settlement.

Verify SolRL as a chain of evidence:

```text
local claim flow proves: worker output -> verifier signature -> token payout semantics -> replay rejection
registry build proves: ClaimV1 checks -> Token-2022 transfer_checked CPI -> registry PDA authority -> slashing path
real AWS proves: EC2 parent -> Nitro EIF boot -> generic compute output -> NSM attestation -> AWS root verification -> PCR16 bridge -> ClaimV1 receipt
```

The current V1 verification path is split this way on purpose. LocalStack cannot fake Nitro, and a real Nitro smoke should not also be the first place you debug Solana token accounts.

The local implementation proves the protocol wiring with mocks:

```text
mock worker -> mock attestation -> verifier signs ClaimV1 -> hook simulator pays -> replay fails
```

Use the local MVP path like this:

```bash
docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh
```

That script drives the first-class CLI. Same flow, fewer ways to accidentally test a different product:

```bash
docker compose run --rm --no-deps harbor-runner \
  python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
```

The Anchor program now compiles the real registry path too:

```text
settle_claim -> verify ClaimV1 -> Token-2022 transfer_checked CPI -> registry PDA authority -> ClaimReceipt paid
slash_operator -> Token-2022 transfer_checked CPI -> stake vault to treasury
```

It does **not** pretend LocalStack can emulate Nitro. LocalStack is used for AWS API workflows like artifact storage and Terraform/IaC tests. Real NSM attestations still require AWS Nitro Enclaves.

## Host Requirements

Only these should be required on the laptop:

- Docker
- Docker Compose
- `.env` with AWS credentials, only when running the real Nitro smoke

Do not install Rust, Anchor, Solana CLI, Node, Python dependencies, Nix, Terraform, or AWS CLI on the host. Use the Compose services.

## Agent Skill

This repo includes a project skill for AI coding tools:

```text
.agents/skills/solrl-framework/          # canonical skill source
.claude/skills/solrl-framework -> ...    # Claude Code symlink
.gemini/skills/solrl-framework -> ...    # Gemini CLI symlink
```

Do not copy the skill into tool-specific folders. Update `.agents/skills/solrl-framework` and let the symlinks point at it. Copies turn one operating manual into three stale ones. Very normal software trap.

Use it when handing this repo to an AI before asking it to run AWS, touch Token-2022, change ClaimV1/PCR16, or edit Docker:

```text
$solrl-framework
```

The skill explains the Docker-only workflow, ClaimV1/PCR16 parity rules, Token-2022 settlement shape, LocalStack boundaries, and real AWS Nitro safety rules. It is intentionally strict about not installing host packages, not adding sidecar AWS scripts, and not touching shared AWS resources without exact SolRL tags.

### Claude Code

Claude Code discovers project skills from `.claude/skills/<skill-name>/SKILL.md`. In this repo that path is a symlink to the canonical `.agents` skill.

From the repo root:

```text
/solrl-framework
```

Then ask the task:

```text
/solrl-framework run the safe local test gate
/solrl-framework update the AWS Nitro smoke without adding sidecar scripts
/solrl-framework explain the ClaimV1/PCR16 settlement flow
```

If Claude Code was already open before the `.claude/skills` directory existed, restart it so it watches the new skill directory.

### Codex

Codex discovers repo skills from `.agents/skills` while working inside the repository. Invoke the skill explicitly with:

```text
$solrl-framework
```

Example prompts:

```text
$solrl-framework inspect the Nitro runner and tell me the safe command to run
$solrl-framework modify ClaimV1 and update every parity test
$solrl-framework run the Docker-only validation gate and summarize failures
```

Codex also reads `agents/openai.yaml` for UI metadata, so keep that file in the canonical skill folder.

### Gemini CLI

Gemini CLI discovers workspace skills from `.agents/skills` and `.gemini/skills`. This repo includes the `.gemini` symlink for explicit compatibility, but `.agents/skills` remains the source of truth.

From the repo root:

```bash
gemini skills list
gemini skills reload
```

Then ask Gemini to use the skill by name:

```text
Use the solrl-framework skill to run the safe local gate.
Use the solrl-framework skill to review AWS Nitro cleanup safety.
```

If a tool shows both `.agents` and `.gemini` entries, prefer the `.agents/skills/solrl-framework` entry. Same target, less indirection.

## Quick Start

For a fast developer check:

```bash
docker compose build dev-shell harbor-runner aws-test-runner
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm harbor-runner pytest -q
docker compose run --rm --no-deps harbor-runner python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh
docker compose up verifier-service
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

`verifier-service` stays running until `docker compose stop verifier-service` or `docker compose down`.

For the full verification path, use the dedicated section below. The quick start is useful, but a reviewer should ask two
sharper questions: "did the token rail actually pay or slash?" and "did a real Nitro enclave produce the attestation?"

For the full development shell:

```bash
docker compose build dev-shell
docker compose run --rm dev-shell bash
```

That image is intentionally heavier. It contains the toolchain needed later for Rust, Anchor, Solana, Node, Python, Terraform, and AWS CLI work.

It does not install the Docker CLI. No default service runs privileged Docker-in-Docker.
The real AWS Nitro runner does not need local build privileges. Nix runs in GitHub Actions for the publish path or in the
dedicated `nix-builder` container for local verification. The AWS runner is not Docker-in-Docker and it does not mount
the host Docker socket.

## Guardrails

Run the full lint gate inside Docker:

```bash
docker compose run --rm lint
```

This checks Rust format, clippy, Ruff, Terraform format/validate, schema parity between Rust and Python, signed-field validation in `settle_claim`, PCR16 recomputation, Token-2022 CPI wiring, the local-validator Token-2022 balance proof, LocalStack honesty, Cargo lockfile compatibility with the SBF builder, neutral public docs language, and the no-Docker-in-Docker boundary.

For Marlin-style reproducible enclave work, use Nix in its own container:

```bash
docker compose run --rm nix-builder nix --version
docker compose run --rm nix-builder nix build --no-link --print-out-paths .#solrl-nitro-worker-eif
```

Keeping Nix separate avoids mixing the Anchor/Solana toolchain with Nix store behavior. The worker EIF is deliberately
small: it uses the pinned Marlin/Oyster vanilla kernel, a static Rust NSM worker, and an `/app` root only. No shell,
`busybox`, package manager, CA bundle, Docker, Python, Node, or Harbor code is copied into this attestation smoke EIF.
The current Docker-built EIF is about 8.6 MiB in the Nix store, with a 9,028,133 byte `image.eif`; the previous
`busybox`-rooted build was 23.2 MiB / 24,299,887 bytes.

The real AWS smoke does not build the EIF on the laptop and no longer rebuilds it on the EC2 hot path. GitHub Actions
builds the EIF from the pinned flake in cacheable stages: static worker, Marlin/Oyster kernel bundle, worker root, then
final EIF. CI uses `DeterminateSystems/magic-nix-cache-action@v13` so Nix store paths produced by one run can be reused by
later runs. The workflow publishes the raw `.eif` plus `.sha384` as a public GHCR OCI artifact, and the EC2 parent pulls
that artifact, verifies the SHA-384 file, and boots it.

The workflow lives at:

```text
.github/workflows/build-nitro-eif.yml
```

It publishes:

```text
ghcr.io/<github-owner>/solrl-nitro-worker-eif:<commit-sha>
```

The package must be public for the no-secret EC2 smoke path. If you intentionally use a private package, pass an explicit
`SOLRL_NITRO_EIF_OCI` only after designing a credential path. Do not sneak registry credentials into EC2 user-data.

To inspect the same cacheable stages locally:

```bash
docker compose run --rm nix-builder nix build --no-link --print-out-paths \
  .#solrl-nitro-worker \
  .#solrl-nitro-kernel-bundle \
  .#solrl-nitro-worker-root \
  .#solrl-nitro-worker-eif
```

Run the staged targets in one `nix-builder` container locally. Separate one-off Compose invocations mirror CI stage names,
but they rehydrate Git inputs and Nix store paths repeatedly on a cold laptop. This is a 200-line config file to prove
we can build a 9 MB enclave. Use the single-container command unless you are debugging one exact stage.

To test workflow wiring locally with `act`:

```bash
act pull_request -W .github/workflows/build-nitro-eif.yml -j build-nitro-eif
```

`.actrc` pins the Ubuntu runner image and amd64 architecture so local workflow checks behave like GitHub-hosted runners.
The `act` pull-request path builds and prepares the EIF artifact, but skips GitHub-only upload and GHCR publish steps
because local `act` does not provide the Actions artifact runtime or `GITHUB_TOKEN` package permissions.

## Dependabot

Dependabot is configured in:

```text
.github/dependabot.yml
```

The root Cargo workspace only allows direct dependency updates. That is intentional. The root `Cargo.lock` contains many
duplicate transitive crates from the Solana `1.18.26` toolchain, including multiple `borsh`, `rand`, and `ring` versions.
Unconfigured Dependabot tries to patch those transitive crates one by one, hits Cargo ambiguity or upstream Solana
constraints, and creates noisy failed `Dependabot Updates` runs.

The useful update lanes are explicit:

- root Cargo workspace: direct Anchor, Solana, SPL, and project dependency updates
- Nitro worker Cargo crate: its standalone direct and transitive Rust updates
- Nix flake inputs: `flake.lock`
- GitHub Actions
- Dockerfiles under `docker/`
- Docker Compose image tags
- Terraform provider lockfile under `infra/localstack`

The lint gate checks this shape with `scripts/check-dependabot-config.py`. It also rejects floating Docker `:latest`
base images, because Dependabot cannot turn `latest` into a meaningful security PR.

## Anchor Program

The first on-chain layer lives in:

- `crates/solrl-claim` — canonical `ClaimV1`, `SlashClaimV1`, and PCR16 hash helpers.
- `programs/solrl-registry` — Anchor registry, verifier policy, image policy, operator, job, lease, claim receipt, nonce receipt, Ed25519 proof check, Token-2022 payout CPI, transfer hook, and slashing path.

Run it entirely inside Docker:

```bash
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

This runs:

```bash
cargo test --workspace --locked
anchor build --no-idl
```

`--no-idl` is intentional right now. The SBF program builds, but Anchor `0.30.1` IDL generation currently trips on its `anchor-syn` / `proc-macro2` path under this Solana `1.18` toolchain. Do not pretend the IDL is done. The binary build is real; the IDL is the next tooling fix.

The current Anchor instruction tests cover registry bootstrap, verifier policy binding, operator auth on leases, on-chain PCR16 computation, Token-2022 extra-account metadata initialization, stake-withdrawal guard rails, and a local-validator Token-2022 balance test. That balance test creates a real Token-2022 mint and token accounts, mints escrow funds, sends an Ed25519 verifier instruction plus `settle_claim`, and asserts escrow goes to zero while the operator payout account receives the claim amount.

The Token-2022 transfer hook is intentionally not used for program-initiated settlement in V1. A `registry -> Token-2022 -> registry hook` path is same-program reentrancy, and Solana rejects it. The enforceable settlement boundary is the registry PDA authority over escrow and stake vault transfers; full hook-side claim verification still needs a PDA seed redesign or a separate hook program.

## Verifier Service

The mock verifier can run as a small HTTP service:

```bash
docker compose up verifier-service
curl -fsS http://localhost:8787/healthz
```

It exposes:

```text
GET  /healthz
POST /verify/mock
```

`POST /verify/mock` accepts a mock Nitro attestation and ClaimV1 context, checks PCR and user-data binding, and returns the verifier signature. This is not the persistent production verifier enclave yet. It is the long-running service shape the operator path can call while real Nitro proof stays on `python -m solrl_core.aws_nitro_runner`.

## Harbor Import Path

Harbor supports custom environments through import paths. SolRL ships this one:

```text
solrl_harbor.nitro_environment:NitroEnvironment
```

Local mode implements Harbor's environment method surface over a deterministic workspace under `artifacts/harbor-nitro/<session_id>`. It supports `start`, `exec`, upload/download, file checks, and `stop`.

AWS mode intentionally errors right now. Production Harbor-over-Nitro still needs the VSOCK worker RPC path and the worker EIF that carries the trusted Harbor runner.

## Real Nitro Smoke

The real AWS gate runs through the same runner path that production will use. Do not add sidecar smoke scripts. A
sidecar can pass while the real launcher still creates untagged resources. That is theater.

Put AWS credentials in `.env`:

```text
AWS_ACCESS_KEY=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

Then run:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
docker compose run --rm aws-nitro-runner
```

The runner refuses to launch from a dirty worktree when it derives the default GitHub source and GHCR EIF artifact. Commit
and push first, wait for `.github/workflows/build-nitro-eif.yml` to publish
`ghcr.io/<owner>/solrl-nitro-worker-eif:<commit-sha>`, then run the smoke. Otherwise AWS would verify yesterday's pushed
EIF while you stare at today's uncommitted code. Classic distributed systems prank.

The runner does a read-only preflight audit before launch. By default it refuses to start if active/stopped
`Project=SolRL` EC2 resources already exist in the account. Use `--allow-existing-solrl` only when you intentionally
want overlapping SolRL runs.

The runner creates one temporary Nitro-enabled EC2 parent with no SSH key, no instance profile, no SSM dependency, and
no inbound security group rules. Every created AWS resource is tagged with `Project=SolRL` and `SolRLRunId=<run id>`,
and cleanup refuses to delete anything whose tags do not match the current run. Post-audit fails the run if exact
run-id resources remain.

Expected duration is about 3-4 minutes when the GHCR EIF artifact exists for the commit. On the default `m5.xlarge`,
that is roughly cents per run. If it fails, start with `artifacts/aws-nitro/<run-id>/console-output.txt` and the last
`SOLRL_PHASE_*` marker. More failure notes live in `.agents/skills/solrl-framework/references/troubleshooting.md`.

The smoke has the EC2 parent clone the configured public Git ref, pull the matching public GHCR EIF artifact with ORAS,
verify its SHA-384 sidecar, boot the EIF, request a real NSM attestation over VSOCK, and verify the COSE signature, AWS
root public key, non-zero PCRs, PCR16 digest, `user_data`, and worker public key on the EC2 parent before printing
success. The local runner reads only the final
`SOLRL_RESULT_BEGIN` / `SOLRL_RESULT_END` console block after the instance stops. Build logs stay on the EC2 root volume
under `/var/log/solrl`; EC2 console is not used as an artifact transport. It only gets small phase-start markers plus the
final result block. LocalStack cannot emulate `/dev/nsm`, PCRs, EIF
boot, VSOCK, or real Nitro attestations.

The Nitro worker handles two separate bindings. It extends PCR16 once with `pcr16_user_data` derived from the same
`Pcr16Components` used by the registry, then places the deterministic generic compute output hash in attestation
`user_data`. The parent verifies `SOLRL_PCR16 == SOLRL_CLAIM_PCR16`, verifies attestation `user_data` against
`SOLRL_COMPUTE_OUTPUT_HASH`, and writes `nitro-claim-receipt.json`. That is the connecting wire between the AWS
attestation rail and the Solana settlement rail.

The EC2 cloud-init script has bounded phases and a hard overall watchdog. A stuck package install, ORAS pull, VSOCK
connect, or verifier call emits a typed failed result block with the current phase and a capped log tail, then shuts the
instance down. The local runner should never wait on a silently wedged EC2 parent.

By default the runner derives the EIF reference from the current GitHub remote owner and commit SHA. Branch names are
rejected for the default path because a branch is not an immutable artifact identity. To use a manually published EIF:

```bash
SOLRL_NITRO_EIF_OCI=ghcr.io/arakoodev/solrl-nitro-worker-eif:<commit-sha> \
  docker compose run --rm aws-nitro-runner
```

## Verification

Use this section as the repeatable verification path for external reviewers and operators.

### 1. Prove The Token Settlement Semantics

Run the local claim path first:

```bash
docker compose run --rm --no-deps harbor-runner python -m solrl_core.cli local-mock --config solrl.toml --work-dir artifacts/mock
```

Check:

- `artifacts/mock/mvp_result.json` has `"status": "paid"` and `"replay_rejected": true`.
- `artifacts/mock/claim_receipt.json` contains the verifier signature and ClaimV1 hash.
- `artifacts/mock/claim_context.json` contains `pcr16` and `pcr16_user_data`.
- `artifacts/mock/hook_state.json` contains one ledger entry with the payout `amount`, `claim_hash`, `job_account`, and
  operator payout token account.

Artifact inspection command:

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
assert state["ledger"][0]["amount"] == receipt["claim"]["amount"]
assert state["ledger"][0]["claim_hash"] == receipt["claim_hash"]
assert state["ledger"][0]["recipient"] == receipt["claim"]["payout_token_account"]
print("LOCAL_TOKEN_PROOF_OK")
print(json.dumps({
    "amount": receipt["claim"]["amount"],
    "claim_hash": receipt["claim_hash"],
    "token_mint": receipt["claim"]["token_mint"],
    "recipient": receipt["claim"]["payout_token_account"],
    "replay_rejected": result["replay_rejected"],
}, indent=2))
PY
```

This is the local token proof. It is intentionally a simulator, not a fake Solana explorer screenshot. It proves the
claim, verifier, payout, and replay behavior that the on-chain program is implementing.

### 2. Prove The On-Chain Token-2022 Implementation Exists

Run:

```bash
docker compose run --rm lint
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
```

Check:

- `scripts/check-token2022-wiring.py` passes. It rejects regressions where `settle_claim` does not invoke Token-2022
  `transfer_checked`, where the registry PDA authority path is removed, or where the local-validator Token-2022 balance
  proof disappears.
- `scripts/check-registry-claim-checks.py` passes. It rejects signed fields that are never checked against registry state.
- `programs/solrl-registry/src/lib.rs` contains the real `settle_claim`, `slash_operator`, `withdraw_stake`,
  `transfer_escrow_to_operator`, and `transfer_stake_to_treasury` paths.
- `programs/solrl-registry/tests/registry_flow.rs` covers registry bootstrap, verifier policy binding, operator auth on
  leases, PCR16 recomputation, Token-2022 extra account metadata creation, stake withdrawal guard rails, and
  `settle_claim_transfers_token2022_balance_with_registry_pda_authority`.

The current honest boundary: the local-validator test proves real Token-2022 balance movement through `settle_claim`, not
hook-side claim verification. V1 uses registry-owned PDA authorities for escrow and stake vaults because same-program
transfer-hook reentry is rejected by Solana. That is the working token settlement proof.

### 3. Prove Real AWS Nitro Attestation

Run the audit and smoke commands in the Real Nitro section, then check:

- `artifacts/aws-nitro/<run-id>/remote-markers.json` has `SOLRL_STATUS=OK`.
- `SOLRL_PCR16` equals `SOLRL_CLAIM_PCR16`.
- `SOLRL_COMPUTE_OUTPUT_HASH` equals the ClaimV1 `trajectory_hash` in `nitro-claim-receipt.json`.
- `SOLRL_ATTESTATION_DOCUMENT_HASH` equals the ClaimV1 `attestation_document_hash` in `nitro-claim-receipt.json`.
- `SOLRL_NITRO_ROOT_SHA256` is present and stable across real Nitro runs.
- Post-audit reports zero exact-run resources left behind.
- `nitro-claim-receipt.json` is the bridge artifact: the real Nitro smoke verified the raw NSM document on EC2, then the
  local runner signs a ClaimV1 receipt whose `pcr16`, `trajectory_hash`, and `attestation_document_hash` are copied from
  the verified remote result markers.

Artifact inspection command:

```bash
RUN_ID=<run-id>
docker compose run --rm --no-deps -T harbor-runner python - <<PY
import json
from pathlib import Path

run_id = "$RUN_ID"
root = Path("artifacts/aws-nitro") / run_id
markers = json.loads((root / "remote-markers.json").read_text())
receipt = json.loads((root / "nitro-claim-receipt.json").read_text())
instance = json.loads((root / "run-instances.json").read_text())["Instances"][0]
postaudit = json.loads((root / "postaudit-project.json").read_text())

assert markers["SOLRL_STATUS"] == "OK"
assert markers["SOLRL_PCR16"] == markers["SOLRL_CLAIM_PCR16"]
assert markers["SOLRL_COMPUTE_OUTPUT_HASH"] == receipt["claim"]["trajectory_hash"]
assert markers["SOLRL_ATTESTATION_DOCUMENT_HASH"] == receipt["claim"]["attestation_document_hash"]
assert markers["SOLRL_EIF_OCI_REF"].endswith(markers["SOLRL_GIT_REF"])
assert instance["EnclaveOptions"]["Enabled"] is True
assert instance["MetadataOptions"]["HttpTokens"] == "required"
tags = {t["Key"]: t["Value"] for t in instance["Tags"]}
assert tags["Project"] == "SolRL"
assert tags["SolRLRunId"] == run_id
assert tags["ManagedBy"] == "SolRL"
assert len(postaudit["instances"]) == 0
assert len(postaudit["security_groups"]) == 0
assert len(postaudit["volumes"]) == 0
print("REAL_NITRO_PROOF_OK")
print(json.dumps({
    "run_id": run_id,
    "instance_id": instance["InstanceId"],
    "eif": markers["SOLRL_EIF_OCI_REF"],
    "nitro_root_sha256": markers["SOLRL_NITRO_ROOT_SHA256"],
    "pcr0": markers["SOLRL_PCR0"],
    "pcr1": markers["SOLRL_PCR1"],
    "pcr2": markers["SOLRL_PCR2"],
    "pcr16": markers["SOLRL_PCR16"],
    "compute_output_hash": markers["SOLRL_COMPUTE_OUTPUT_HASH"],
    "claim_hash": receipt["claim_hash"],
}, indent=2))
PY
```

This proves the AWS rail: a Nitro-enabled EC2 parent booted the commit-pinned EIF, ran deterministic generic compute inside
the enclave, got a real NSM attestation with the compute output in `user_data`, verified COSE/AWS root/PCRs/user data, and
left no tagged AWS residue. The resulting ClaimV1 receipt is ready to feed into the same settlement shape tested by the
local Token-2022 registry test.

### 4. Token Verification Status

The current status is:

```text
The protocol token is represented by a Token-2022 mint in the registry config. Jobs escrow that token, operators register
payout and stake token accounts, verified claims call settle_claim, and settle_claim performs a Token-2022 transfer_checked
CPI from job escrow to operator payout. Slashing performs the same Token-2022 transfer_checked CPI from operator stake to
treasury. The local MVP proves the claim-to-payout semantics with a deterministic hook simulator and replay rejection.
The Anchor program now has a local-validator Token-2022 balance test proving `settle_claim` moves real token balances in
one transaction. The transfer hook is not part of that program-initiated settlement path; the registry PDA authority is the
token boundary for V1.
```

## Docker-in-Docker

Default services avoid privileged Docker-in-Docker.

The current `dev-shell` image does not include the Docker CLI. If a future step needs a container to build or run other containers, prefer mounting the host Docker socket into a dedicated service and document the trust cost. Do not silently add privileged `docker:dind`.

The `aws-nitro-runner` service does not need local Linux build privileges now. The local Docker service does not run
Docker, does not mount the Docker socket, and does not run with `privileged: true`.
