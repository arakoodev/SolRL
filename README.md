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

## Quick Start

```bash
docker compose build harbor-runner aws-test-runner
docker compose up -d localstack
docker compose run --rm lint
docker compose run --rm harbor-runner pytest -q
docker compose run --rm harbor-runner ./scripts/e2e-local-mock.sh
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

## Guardrails

Run the full lint gate inside Docker:

```bash
docker compose run --rm lint
```

This checks Rust format, clippy, Ruff, Terraform format/validate, schema parity between Rust and Python, signed-field validation in `settle_claim`, Token-2022 wiring, LocalStack honesty, and the no-Docker-in-Docker boundary.

For Marlin-style reproducible enclave work, use Nix in its own container:

```bash
docker compose run --rm nix-builder nix --version
```

Keeping Nix separate avoids mixing the Anchor/Solana toolchain with Nix store behavior.

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

One more honest gap: the Token-2022 path compiles, but there is not yet a full local-validator transaction test proving Token-2022 invokes the hook end-to-end. The Python mock e2e covers the protocol shape. The next engineering step is a real validator test.

The transfer hook is intentionally a mint-level guard in V1. Token-2022 hook execute data only carries `amount`, and the mint-wide extra account list cannot pass arbitrary per-job accounts unless the PDA graph is derivable from the transfer source/mint/destination/owner. Full hook-side claim verification needs that seed redesign.

## Real Nitro Smoke

This is intentionally not runnable on a normal laptop:

```bash
docker compose run --rm harbor-runner ./scripts/e2e-aws-nitro.sh --cluster devnet
```

It exits with a clear message unless it is run on an AWS host with Nitro Enclaves tooling. LocalStack cannot emulate `/dev/nsm`, PCRs, EIF boot, or real Nitro attestations.

## Docker-in-Docker

Default services avoid privileged Docker-in-Docker.

The current `dev-shell` image does not include the Docker CLI. If a future step needs a container to build or run other containers, prefer mounting the host Docker socket into a dedicated service and document the trust cost. Do not silently add privileged `docker:dind`.
