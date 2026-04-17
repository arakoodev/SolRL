# SolRL Harbor TEE Protocol Plan

## Point

Build a Solana-native protocol for Harbor evals running inside AWS Nitro Enclaves.

What it does:
- A researcher funds a Harbor eval bounty.
- An operator runs one deterministic Harbor eval inside one AWS Nitro worker enclave.
- The worker produces an AWS Nitro attestation that binds the reward, task digest, artifact hashes, operator, job, and payout context.
- A verifier enclave checks the Nitro attestation off-chain.
- The registry validates the claim and releases payout through Token-2022.
- A Token-2022 transfer hook guards token movement at the mint boundary.

Why it matters:
RL eval markets only work if the reward is hard to fake. Regular containers can be fast and cheap, but the host can lie. Nitro gives a hardware-rooted proof path. Solana gives fast settlement.

What changes for the builder:
The first implementation must prove the whole loop, not just show a dashboard.

```text
create job
  -> run deterministic Harbor eval in Nitro
  -> verifier signs ClaimV1
  -> registry settles through Token-2022
  -> replay fails
  -> artifact hash matches
```

That is the product.

## Source Baselines

Clone these pinned upstreams into the repo once implementation starts:

```text
marlinprotocol/oyster-monorepo
  ref: f60874a27f56eee2974cf0099ff80e9627c8f1dd

marlinprotocol/oyster-solana-contracts
  ref: 73eb72337cfe4985b4cc532b0f45287d9982a5b8

harbor-framework/harbor
  ref: 3396e6f1f82b831108d26b0273d24f5424519f86
```

Important source finding:
- `oyster-solana-contracts` does not contain a ready `EnclaveRegistry`.
- Marlin's useful pieces are in `oyster-monorepo`: attestation server, custom server, verifier, verifier enclave, Blue image, PCR16 tools, kernels, and AWS setup.
- Harbor already supports external environments via `import_path`, so do not fork Harbor first.

## Docker-First Rule

No package installs on the laptop.

The laptop should only need:
- Docker Engine
- Docker Compose

Everything else runs inside containers:
- Rust
- Anchor
- Solana CLI
- Node
- Python
- Nix
- Terraform
- AWS CLI
- LocalStack clients
- Harbor development dependencies

Use named Docker volumes for caches:

```text
cargo-cache
target-cache
npm-cache
pip-cache
solana-ledger
localstack-data
```

Do not write build outputs into random host directories. Put generated artifacts under repo-owned paths like:

```text
artifacts/
artifacts/eif/
artifacts/idl/
artifacts/manifests/
artifacts/logs/
```

## Docker Compose Architecture

```text
Host laptop
  |
  | docker compose up
  v
+------------------------------------------------------------+
| Docker Compose                                             |
|                                                            |
|  dev-shell                                                 |
|    Rust, Anchor, Solana CLI, Node, Python, Terraform       |
|    mounts repo + named caches                              |
|                                                            |
|  nix-builder                                               |
|    Nix + flakes for Marlin reproducible enclave work       |
|                                                            |
|  solana-test-validator                                     |
|    local chain for Anchor + Token-2022 tests               |
|                                                            |
|  localstack                                                |
|    S3/IAM/Lambda/Dynamo-style AWS API tests where useful   |
|                                                            |
|  aws-test-runner                                           |
|    runs Terraform tests against LocalStack                 |
|                                                            |
|  harbor-runner                                             |
|    runs Harbor plugin tests and local mock evals           |
|                                                            |
|  verifier-mock                                             |
|    runs Marlin verifier logic against fixtures             |
|                                                            |
|  worker-mock                                               |
|    simulates Nitro runner with server-custom-mock          |
+------------------------------------------------------------+
```

### Docker-in-Docker Status

This plan should avoid Docker-in-Docker for normal development.

Implemented approach:
- Run Docker Compose from the host.
- Tooling runs inside `dev-shell`.
- `dev-shell` does not install the Docker CLI.
- No service is privileged.
- No `docker:dind` service exists in the default compose file.

This keeps the laptop rule clean: Docker and Docker Compose on the host, everything else inside containers.

If we later need real Docker-in-Docker:
- Use a separate `docker:dind` service.
- Mark it explicitly as privileged.
- Keep it out of default `docker compose up`.

Do not hide this. If implementation reaches a true DinD requirement, stop and ask.

## LocalStack Role

Use LocalStack for local AWS API workflows:
- S3-compatible artifact storage tests.
- Terraform/IaC validation.
- IAM-style config shape where LocalStack supports it.
- Lambda/Step Functions/Dynamo only if the operator flow later needs them.

AWS Prescriptive Guidance says LocalStack runs as a Docker container and helps test Terraform/IaC without provisioning real AWS resources. It also calls out that service feature coverage varies by service.

Do not claim LocalStack can emulate:
- AWS Nitro Enclaves.
- `/dev/nsm`.
- Real NSM attestations.
- PCR measurements.
- Real EIF boot.
- Real Nitro parent/enclave VSOCK behavior.

So the test split is:

```text
LocalStack:
  artifact bucket
  Terraform tests
  local AWS-like API integration

Mock Nitro:
  server-custom-mock
  verifier fixtures
  claim signing
  Solana settlement

Real AWS:
  actual Nitro enclave boot
  actual NSM attestation
  actual PCR16
  actual VSOCK path
```

The real AWS smoke test stays mandatory before demo/release.

## Protocol Architecture

```text
Researcher
  |
  v
Solana Job Escrow --timed lease--> Staked Operator
  |                                  |
  |                                  v
  |                         Protocol AWS parent EC2
  |                                  |
  |                                  v
  |                         one Worker EIF per eval
  |                                  |
  |                                  v
  |                  trusted runner executes Harbor task
  |                                  |
  |                                  v
  |                    NSM attests ClaimV1 hash bundle
  |                                  |
  |                                  v
Verifier EIF checks AWS attestation + PCR policy, signs ClaimV1 with Ed25519
  |
  v
SolRL registry checks ClaimV1 + instructions sysvar + job state
  |
  v
payout or reject/slash by failure taxonomy
```

## V1 Trust Model

Be honest in docs and demo:

V1 is centrally administered.

V1 uses:
- Single dev key for upgrade, mint, hook config, treasury, verifier registry, and image registry.
- Protocol-managed AWS only.
- Admin registry for verifier keys and image policies.
- Multiple active verifier keys, using `VerifierPolicy::AnyActive { family, version }`.

V1 is not:
- Fully decentralized.
- Governance-controlled.
- BYO AWS operator-ready.
- A fully on-chain Nitro verifier.
- A full dispute-resolution protocol.

The thing V1 proves is the TEE proof-to-payout path.

## Core On-Chain Accounts

Current implementation lives in `programs/solrl-registry`.

Implemented now:
- `Config`
- `VerifierPolicy`
- `Verifier`
- `ImagePolicy`
- `Operator`
- `Job`
- `Lease`
- `ClaimReceipt`
- `NonceReceipt`
- `ClaimV1` shared Rust schema in `crates/solrl-claim`
- `SlashClaimV1` shared Rust schema in `crates/solrl-claim`
- PCR16 component hashing shared across Rust and Python
- Claim schema parity lints shared across Rust and Python
- ClaimV1 golden vectors shared across Rust and Python tests
- Signed-field validation lint for `settle_claim`
- Token-2022 wiring lint for payout CPI and hook guard behavior
- Docker boundary lint for no Docker-in-Docker and LocalStack honesty
- Ed25519 instruction-sysvar parser for verifier signatures
- Token-2022 `ExtraAccountMetaList` initializer
- Token-2022 transfer hook fallback router and mint-level guard
- Token-2022 escrow payout CPI from `settle_claim`
- Token-2022 stake slash CPI from `slash_operator`
- Failure taxonomy for reject-only vs slashable claim failures

Not implemented yet:
- Full local-validator instruction test that proves Token-2022 calls the hook end-to-end.
- Full hook-as-verifier mode. Token-2022 hook execute data only carries `amount`, and the mint-wide `ExtraAccountMetaList` can only pass accounts resolvable from source, mint, destination, owner, instruction data, or prior resolved accounts. The current job PDA graph is not derivable from those values. V1 therefore verifies the claim in `settle_claim` and uses the hook as a mint-level guard.
- Anchor IDL generation. `anchor build --no-idl` passes; full IDL generation currently hits an Anchor `0.30.1` / `proc-macro2` compatibility problem.
- Production Harbor Nitro environment plugin.
- Real AWS Nitro EIF boot and NSM smoke.

### `Config`

Stores:
- Admin authority.
- Token-2022 mint.
- Transfer hook program id.
- Treasury token account.
- Pause flag.
- Verifier policy mode.
- Static resource class cost table.
- Protocol version.

### `Operator`

Stores:
- Operator owner.
- Payout token account.
- Stake amount.
- Active lease count.
- Status.
- Slash history counters.

### `Job`

Stores:
- Researcher owner.
- Token escrow account.
- Task hash.
- Resource class.
- Required image policy id.
- Required verifier policy id.
- Lease state.
- Bounty amount.
- Timeout.
- Artifact policy.

### `Lease`

Stores:
- Job.
- Operator.
- Attempt nonce.
- Expiry.
- State.

### `ImagePolicy`

Stores:
- PCR0.
- PCR1.
- PCR2.
- PCR16.
- Verifier-computed image id.
- Family.
- Version.
- Active flag.

### `VerifierPolicy`

V1:

```text
AnyActive {
  family,
  version
}
```

Later:

```text
Threshold {
  family,
  version,
  threshold,
  members
}
```

Do not build threshold in v1.

### `ClaimReceipt`

Stores:
- Claim hash.
- Payout signature status.
- Transfer status.
- Slash/reject reason if applicable.
- Artifact hashes.
- Close authority.

## ClaimV1

One canonical schema.

Create a shared Rust crate:

```text
crates/solrl-claim/
```

It defines:
- `ClaimV1`.
- Fixed byte serialization.
- Domain separator.
- Protocol version.
- Hash helper.
- Golden vectors.

Use Ed25519 for verifier signatures.

`ClaimV1` must include:
- Cluster hash.
- Program id.
- Token-2022 mint.
- Hook program id.
- Job account.
- Lease account.
- ClaimReceipt account.
- Operator account.
- Operator payout token account.
- Amount.
- Resource class hash.
- Verifier policy id.
- Image policy id.
- Worker enclave public key hash.
- Task hash.
- Reward script hash.
- Harbor environment hash.
- Artifact policy hash.
- Network policy hash.
- Attestation document hash.
- Trajectory hash.
- Reward value.
- Lease expiry.
- Claim expiry.
- Nonce.
- Protocol version.

This is not optional. Replay bugs are protocol killers.

## PCR16 Binding

PCR16 task digest must include:
- Job id.
- Lease id.
- Attempt nonce.
- Harbor task bundle hash.
- `task.toml` hash.
- `instruction.md` hash.
- `tests/test.sh` hash.
- Reward script hash.
- Harbor environment/plugin hash.
- Resource class.
- Timeout.
- Network policy.
- Operator account.
- Payout token account.
- Token mint.
- Artifact policy.
- Protocol version.

V1 network policy:

```text
DenyAll
```

Future allowlists must be included in PCR16 and `ClaimV1`.

## Settlement Choreography

Use one atomic transaction.

```text
[0] Ed25519 verify instruction
    verifies verifier signature over ClaimV1 bytes

[1] solrl_registry::settle_claim
    checks job, lease, operator, amount, nonce, expiry
    checks cluster, mint, hook program, verifier policy, image policy
    checks PCR16, artifact hashes, network policy, worker key hash
    creates ClaimReceipt PDA
    creates NonceReceipt PDA

    CPI: token_2022::transfer_checked
      escrow -> operator payout token account

      Token-2022 invokes transfer hook
        checks Token-2022 transfer hook `transferring` flag
        checks mint matches protocol config

    after CPI success:
      marks ClaimReceipt paid
      marks NonceReceipt consumed
      marks Job settled
      marks Lease settled
```

If this cannot be made atomic during implementation, stop and ask.

Do not ship a two-phase path where a claim is accepted and payout can fail later without recovery.

### Transfer Hook Limitation

The first design tried to put the entire claim check inside the Token-2022 hook.

That does not work with the current PDA graph.

Token-2022 does not forward every account appended to `transfer_checked`. It reads the mint's `ExtraAccountMetaList`, resolves only those accounts, and invokes the hook with that resolved list. Since hook execute data only contains `amount`, dynamic accounts like `Job`, `Lease`, `ClaimReceipt`, `VerifierPolicy`, and `ImagePolicy` must be derivable from source, mint, destination, owner, instruction data, or already resolved accounts.

Current SolRL jobs are keyed by `job_id`, not by the escrow token account. That means the hook cannot derive the job account from the standard transfer hook inputs.

V1 chooses the boring path that works:
- `settle_claim` verifies `ClaimV1` and owns the escrow transfer.
- Token-2022 still moves the tokens.
- The transfer hook is a mint-level guard, not the claim verifier.

To make the hook itself verify claims later, redesign PDA seeds so the whole graph is derivable from the transfer's source token account:

```text
source token -> job PDA -> lease PDA -> receipt PDA -> policy PDAs
```

## Slashing

Do not use one bucket called "failed claim."

Use three classes.

### Slashable

Examples:
- Replay.
- Malicious duplicate.
- Signed claim for wrong policy.
- Signed claim for wrong job.
- Lease abuse.
- Forged context.

### Reject Only

Examples:
- Malformed transaction.
- Missing account.
- Expired non-malicious claim.
- Insufficient compute.
- Precompile failure before program can run.

### Dispute Only

Examples:
- Artifact unavailable.
- Suspected verifier/operator collusion.
- Parent host censorship.
- Verifier liveness failure.

V1 has no full dispute system.

V1 must document that.

## Enclave Design

### Verifier Enclave

Start from Marlin:

```text
attestation/verifier
attestation/verifier-enclave
```

Keep:
- AWS root validation.
- CBOR/COSE parsing.
- X.509 chain checks.
- PCR parsing.
- Nix reproducible EIF pipeline.

Change:
- Stop signing EIP-712/secp256k1 payloads.
- Sign `ClaimV1` using Ed25519.
- Return:
  - claim hash
  - Ed25519 signature
  - verifier pubkey
  - decoded PCRs
  - image policy id
  - attestation document hash
  - reason codes

### Worker Enclave

Start from Marlin Blue image:

```text
enclaves/blue
```

V1 invariant:

```text
one Harbor eval = one worker enclave boot = one PCR16 digest = one claim
```

Worker components:
- `solrl-runner`
- `solrl-attestor`
- Harbor task executor
- deterministic reward verifier

The agent must never call:
- `server-custom`
- `/dev/nsm`
- claim attestation endpoint

The trusted runner computes reward and claim bytes, then asks the attestor to seal them.

## Harbor Integration

Do not fork Harbor first.

Ship a plugin implementing Harbor's environment interface via `import_path`.

```text
python/solrl_harbor/
  nitro_environment.py
```

Responsibilities:
- Start worker enclave.
- Upload Harbor task bundle.
- Execute commands over VSOCK RPC.
- Download artifacts.
- Stop enclave.
- Surface typed errors.

V1 deterministic reward:
- use Harbor `tests/test.sh`
- no LLM judge
- no internet

## Docker Compose Services

Implemented Docker files:

```text
docker-compose.yml
docker/dev-shell.Dockerfile
docker/python-runner.Dockerfile
docker/aws-test-runner.Dockerfile
scripts/lint.sh
```

### `dev-shell`

Contains:
- Rust toolchain.
- Anchor CLI.
- Solana CLI.
- Node.
- Python.
- Terraform.
- AWS CLI.
- `awslocal`.
- No Docker CLI.

Mounts:
- repo
- named caches

No host package installs.

### `lint`

Runs inside the `dev-shell` image:
- `cargo fmt --check`
- `cargo clippy`
- Ruff
- Terraform format and validate
- Rust/Python claim schema parity
- `settle_claim` signed-field coverage
- Token-2022 wiring checks
- Docker boundary checks

### `nix-builder`

Runs Nix in its own container:

```bash
docker compose run --rm nix-builder nix --version
```

Use this for Marlin `verifier-enclave` and reproducible EIF work. Keeping it separate from `dev-shell` avoids mixing the Anchor/Solana toolchain with Nix store behavior.

### `solana-test-validator`

Runs local validator.

Reserved for:
- Full Anchor instruction tests.
- Token-2022 hook transaction tests.

Current Docker tests compile the program and run mock e2e without a validator.

### `localstack`

Runs:
- S3-compatible artifact tests.
- Terraform tests.

Default services:

```text
SERVICES=s3,iam,sts
```

Only add more services when code needs them.

### `aws-test-runner`

Runs:

```bash
terraform init
terraform validate
terraform fmt -recursive -check
terraform test
```

Against LocalStack endpoints.

### `verifier-mock`

Runs Marlin verifier tests and SolRL `ClaimV1` signing tests against fixtures.

### `worker-mock`

Runs:
- `server-custom-mock`
- fake NSM fixture path
- deterministic Harbor task smoke
- trusted runner boundary tests

This does not prove real Nitro.

It proves local protocol wiring.

## Commands

Developer entrypoints should be Docker-only:

```bash
docker compose build
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
docker compose run --rm --no-deps harbor-runner pytest -q
docker compose run --rm --no-deps harbor-runner ./scripts/e2e-local-mock.sh
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
docker compose run --rm nix-builder nix --version
docker compose run --rm harbor-runner ./scripts/e2e-aws-nitro.sh --cluster devnet
```

No `cargo`, `anchor`, `solana`, `npm`, `pip`, `nix`, `terraform`, or `aws` commands should be required on the host.

## Test Plan

```text
CODE PATH COVERAGE TARGET
=========================
Anchor registry/hook/market     100%
Shared ClaimV1 schema           100%, golden vectors
Verifier enclave service        AWS fixture, PCR16 fixture, bad root, stale attestation
Worker enclave runner           trusted-only attestation, timeout, failed verifier script
Harbor Nitro environment        start, exec, upload, download, stop, typed errors
End-to-end local mock           required in CI
AWS Nitro smoke                 manual/nightly, required before demo/release
```

Required test commands:

```bash
docker compose run --rm --no-deps dev-shell ./scripts/test-anchor.sh
docker compose run --rm lint
docker compose run --rm --no-deps harbor-runner pytest -q
docker compose run --rm --no-deps harbor-runner ./scripts/e2e-local-mock.sh
docker compose up -d localstack
docker compose run --rm harbor-runner ./scripts/test-localstack.sh
docker compose run --rm aws-test-runner
docker compose run --rm nix-builder nix build .#gnu.attestation.verifier-enclave.default
docker compose run --rm nix-builder nix build .#gnu.enclaves.solrl-worker.default
docker compose run --rm harbor-runner ./scripts/e2e-aws-nitro.sh --cluster devnet
```

Current test reality:
- `scripts/lint.sh` runs style checks, Terraform validation, schema parity checks, signed-field coverage checks, Token-2022 wiring checks, and Docker boundary checks.
- `scripts/test-anchor.sh` runs `cargo test --workspace` and `anchor build --no-idl`.
- Rust and Python tests share a ClaimV1 golden vector.
- Python tests cover canonical ClaimV1 signing, PCR16 composition, replay rejection, wrong cluster rejection, and slash claim signing.
- `scripts/e2e-local-mock.sh` proves the local worker -> verifier -> hook simulator path.
- A full Token-2022 validator transaction test is still missing. This is the next correctness gap, not a nice-to-have.

## Failure Modes To Test

- Wrong verifier.
- Revoked verifier.
- Inactive image.
- Stale timestamp.
- Wrong recipient.
- Wrong amount.
- Wrong job.
- Replayed nonce.
- Lease expiry.
- Duplicate lease.
- Unstake during active lease.
- Insufficient bounty.
- Nitro boot failure.
- VSOCK timeout.
- Verifier unavailable.
- Solana RPC timeout.
- Agent attempts direct attestation and is denied.
- Enclave exits from resource exhaustion.

User-facing result must be a typed failure, not silence.

## LocalStack Tests

LocalStack tests should cover:
- Artifact bucket creation.
- Artifact upload/download.
- Content-addressed object key.
- Retention metadata.
- Terraform validation.
- IAM policy shape where supported.

LocalStack tests should not claim to cover:
- Nitro enclave launch.
- NSM attestation.
- EIF PCR correctness.
- Nitro VSOCK correctness.

## Not In Scope

- Production release bundle.
- Threshold verifier signatures.
- Worker enclave warm pools or reuse.
- LLM-judge rewards.
- Verifier self-registration and governance.
- BYO AWS operator onboarding.
- Full dispute adjudication.
- Open-internet Harbor tasks.
- LocalStack simulation of Nitro.

## Deferred TODOs

### Production release bundle

Publish Anchor IDL, operator image, Harbor plugin package, verifier EIF, worker EIF, PCR metadata, and checksums.

### Threshold verifier signatures

Add `VerifierPolicy::Threshold` after any-one verifier policy is stable.

### Worker enclave warm pools

Benchmark cold start and runtime first. Optimize only after correctness.

### LLM judge rewards

Add prompt eval baselines only after deterministic rewards work.

### Verifier self-registration and governance

Move beyond dev-key admin registry after the proof path works.

## Parallel Workstreams

| Step | Modules | Depends On |
|---|---|---|
| Shared schema + manifest | `crates/`, `scripts/` | none |
| Docker dev shell + compose | `docker/`, `docker-compose.yml` | none |
| Anchor registry + hook | `programs/`, `tests/` | schema |
| Verifier enclave signing | `attestation/`, `enclaves/` | schema |
| Worker runner | `enclaves/`, `runner/` | schema |
| Harbor plugin/operator | `python/`, `operator/` | runner RPC shape |
| LocalStack/Terraform tests | `infra/`, `scripts/` | compose |
| AWS smoke scripts | `scripts/`, `infra/` | Anchor + verifier + worker |

Suggested execution:

```text
Lane A: Docker compose + dev-shell
Lane B: ClaimV1 schema
Lane C: Anchor registry/hook after B
Lane D: verifier signing after B
Lane E: worker runner after B
Lane F: Harbor plugin after E
Lane G: local mock e2e after C + D + E
Lane H: AWS smoke after G
```

## Review Status

```text
Eng Review: complete
Outside Voice: complete
Major concerns resolved into plan:
  - hook atomicity
  - slashing taxonomy
  - Ed25519 verifier signatures
  - PCR16 full binding
  - no-internet v1
  - single dev key v1 honesty
  - content-addressed artifacts
  - protocol-managed AWS only
```

## Sources

- AWS LocalStack Terraform testing pattern: https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/test-aws-infra-localstack-terraform.html
- Marlin Oyster monorepo: https://github.com/marlinprotocol/oyster-monorepo
- Marlin Solana contracts baseline: https://github.com/marlinprotocol/oyster-solana-contracts
- Harbor: https://github.com/harbor-framework/harbor
- Daytona fingerprinting: https://github.com/diggerhq/sandbox-fingerprinting/blob/main/daytona-fingerprint-findings.md
- Sysbox: https://github.com/nestybox/sysbox
