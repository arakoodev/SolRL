# SolRL

SolRL is a Docker-first scaffold for a Harbor evaluation protocol backed by AWS Nitro attestations and Solana Token-2022 settlement.

The local implementation proves the protocol wiring with mocks:

```text
mock worker -> mock attestation -> verifier signs ClaimV1 -> hook simulator pays -> replay fails
```

The Anchor program now compiles the real registry path too:

```text
settle_claim -> verify ClaimV1 -> Token-2022 transfer_checked CPI -> hook guard -> ClaimReceipt paid
slash_operator -> Token-2022 transfer_checked CPI -> stake vault to treasury
```

It does **not** pretend LocalStack can emulate Nitro. LocalStack is used for AWS API workflows like artifact storage and Terraform/IaC tests. Real NSM attestations still require AWS Nitro Enclaves.

## Host Requirements

Only these should be required on the laptop:

- Docker
- Docker Compose

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

```bash
docker compose build dev-shell harbor-runner aws-test-runner
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm harbor-runner pytest -q
docker compose run --rm --no-deps dev-shell ./scripts/e2e-local-mock.sh
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
```

For the full development shell:

```bash
docker compose build dev-shell
docker compose run --rm dev-shell bash
```

That image is intentionally heavier. It contains the toolchain needed later for Rust, Anchor, Solana, Node, Python, Terraform, and AWS CLI work.

It does not install the Docker CLI. No default service runs privileged Docker-in-Docker.
The real AWS Nitro runner does not need local build privileges. Nix runs on the temporary EC2 parent, not in a privileged
local container. It is not Docker-in-Docker and it does not mount the host Docker socket.

## Guardrails

Run the full lint gate inside Docker:

```bash
docker compose run --rm lint
```

This checks Rust format, clippy, Ruff, Terraform format/validate, schema parity between Rust and Python, signed-field validation in `settle_claim`, PCR16 recomputation, Token-2022 `TransferGuard` wiring, LocalStack honesty, Cargo lockfile compatibility with the SBF builder, and the no-Docker-in-Docker boundary.

For Marlin-style reproducible enclave work, use Nix in its own container:

```bash
docker compose run --rm nix-builder nix --version
docker compose run --rm nix-builder nix build --no-link --print-out-paths .#solrl-nitro-worker-eif
```

Keeping Nix separate avoids mixing the Anchor/Solana toolchain with Nix store behavior. The real AWS smoke does not
build the EIF on the laptop. The EC2 parent clones the configured Git ref and rebuilds the EIF there before booting it.

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
cargo test --workspace
anchor build --no-idl
```

`--no-idl` is intentional right now. The SBF program builds, but Anchor `0.30.1` IDL generation currently trips on its `anchor-syn` / `proc-macro2` path under this Solana `1.18` toolchain. Do not pretend the IDL is done. The binary build is real; the IDL is the next tooling fix.

The current Anchor instruction tests cover registry bootstrap, verifier policy binding, operator auth on leases, on-chain PCR16 computation, Token-2022 extra-account metadata initialization, and stake-withdrawal guard rails. The Python mock e2e covers the full off-chain protocol shape, then runs the registry instruction tests in the same Docker entrypoint.

One honest remaining gap: there is not yet a full local-validator transaction test proving Token-2022 invokes the hook end-to-end. V1 verifies claims in `settle_claim`, arms a one-use `TransferGuard`, flushes it before the CPI, and requires the hook to consume that guard. Full hook-side claim verification still needs a PDA seed redesign so the hook can derive the job, lease, receipt, and policy graph from the transfer inputs.

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

The runner does a read-only preflight audit before launch. By default it refuses to start if active/stopped
`Project=SolRL` EC2 resources already exist in the account. Use `--allow-existing-solrl` only when you intentionally
want overlapping SolRL runs.

The runner creates one temporary Nitro-enabled EC2 parent with no SSH key, no instance profile, no SSM dependency, and
no inbound security group rules. Every created AWS resource is tagged with `Project=SolRL` and `SolRLRunId=<run id>`,
and cleanup refuses to delete anything whose tags do not match the current run. Post-audit fails the run if exact
run-id resources remain.

The smoke has the EC2 parent clone the configured public Git ref, install Nix on the EC2 parent, rebuild the
`solrl-nitro-worker-eif` through `monzo/aws-nitro-util` and the pinned Marlin/Oyster kernel path, boot the EIF, request a
real NSM attestation over VSOCK, and verify the COSE signature, AWS root public key, non-zero PCRs, PCR16 digest,
`user_data`, and worker public key on the EC2 parent before printing success. The local runner reads only the final
`SOLRL_RESULT_BEGIN` / `SOLRL_RESULT_END` console block after the instance stops. Build logs stay on the EC2 root volume
under `/var/log/solrl`; EC2 console is not used as an artifact transport. It only gets small phase-start markers plus the
final result block. LocalStack cannot emulate `/dev/nsm`, PCRs, EIF
boot, VSOCK, or real Nitro attestations.

The EC2 cloud-init script has bounded phases and a hard overall watchdog. A stuck package install, Nix build, VSOCK
connect, or verifier call emits a typed failed result block with the current phase and a capped log tail, then shuts the
instance down. The local runner should never wait on a silently wedged EC2 parent.

## Docker-in-Docker

Default services avoid privileged Docker-in-Docker.

The current `dev-shell` image does not include the Docker CLI. If a future step needs a container to build or run other containers, prefer mounting the host Docker socket into a dedicated service and document the trust cost. Do not silently add privileged `docker:dind`.

The `aws-nitro-runner` service does not need local Linux build privileges now. Nix runs on the temporary EC2 parent for
the real smoke. The local Docker service does not run Docker, does not mount the Docker socket, and does not run with
`privileged: true`.
