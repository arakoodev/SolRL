# SolRL Architecture

## Local Mock Flow

```text
Harbor-style task
    |
    v
python/solrl_core/mock_worker.py
    |
    | emits mock-nitro-attestation-v1
    v
python/solrl_core/verifier_service.py or python/solrl_core/mock_verifier.py
    |
    | signs canonical ClaimV1 bytes
    v
python/solrl_core/mock_hook.py
    |
    | simulates payout + replay rejection
    v
tests/test_claim_flow.py
```

This proves the protocol shape without pretending to be Nitro.

The single command path is:

```text
scripts/e2e-local-mock.sh
    |
    v
python -m solrl_core.cli local-mock
    |
    +--> mock worker
    +--> verifier signing
    +--> hook simulator payout
    +--> replay rejection
    +--> registry instruction tests
```

## Harbor Environment Surface

```text
Harbor EnvironmentFactory
    |
    | import_path="solrl_harbor.nitro_environment:NitroEnvironment"
    v
python/solrl_harbor/nitro_environment.py
    |
    +--> local mode: deterministic workspace transport
    +--> aws mode: rejected until VSOCK worker RPC is implemented
```

Do not fork Harbor first. The import-path class is the integration seam.

## Real Registry Flow

```text
ClaimV1 bytes
    |
    v
programs/solrl-registry::settle_claim
    |
    +--> verify Ed25519 proof
    +--> verify verifier policy
    +--> recompute PCR16 from registered components
    +--> cross-check Job, Lease, Operator, ImagePolicy fields
    +--> create ClaimReceipt / NonceReceipt
    +--> arm TransferGuard
    +--> Token-2022 transfer_checked CPI
    +--> transfer hook consumes TransferGuard
```

Slashing follows the same idea: a signed `SlashClaimV1` moves stake from the operator stake vault to treasury.

## Real AWS Nitro Smoke

```text
local Docker aws-nitro-runner
    |
    | boto3, .env credentials
    v
temporary EC2 parent
    |
    | tagged Project=SolRL, SolRLRunId=<run>
    | no SSH key, no inbound SG, no instance profile
    v
cloud-init
    |
    +--> install Nitro CLI + ORAS
    +--> git clone public SolRL ref
    +--> pull public GHCR EIF artifact
    +--> sha384sum -c artifact sidecar
    +--> nitro-cli run-enclave
    +--> VSOCK request to worker
    +--> verify COSE / AWS root / PCRs / user_data
    +--> assert SOLRL_PCR16 == SOLRL_CLAIM_PCR16
    +--> emit final result block
    +--> shutdown
```

The laptop never builds the real EIF for the smoke. GitHub Actions builds the EIF from the pinned flake and publishes the
raw `.eif` as an OCI artifact. The EC2 parent clones the same git ref for verifier code identity, pulls the matching EIF,
verifies the SHA-384 sidecar, then boots it.

CI builds the EIF in cacheable Nix stages:

```text
static worker -> Marlin/Oyster kernel bundle -> app root -> final EIF
```

The workflow uses Magic Nix Cache on GitHub runners. Local `act` skips that cache step because `act` does not provide the
same GitHub cache runtime, but it still exercises the staged build and artifact preparation path.

The attestation smoke EIF runtime root is intentionally tiny:

```text
/
+-- app/
    +-- solrl-nitro-worker   static Rust binary
```

There is no shell, `busybox`, package manager, CA bundle, Docker, Python, Node, or Harbor code in this smoke EIF. Build
dependencies can still mention cross-platform packages in Nix logs. That does not mean they are present in the runtime
root. As of the optimized build, the output is about 8.6 MiB in the Nix store with a 9,028,133 byte `image.eif`.

## Marlin/Oyster Baseline

SolRL follows the useful pieces from Marlin Oyster:

- Build EIFs with Nix and `monzo/aws-nitro-util`, not `nitro-cli build-enclave`.
- Keep the kernel/image path reproducible.
- Treat raw AWS Nitro attestation verification as off-chain work.
- Keep the on-chain program focused on lightweight signed claims, PCR/policy matching, replay protection, staking, and settlement.

Do not copy Marlin blindly. SolRL is narrower: Harbor evals and RL settlement, not general-purpose TEE compute.

## Verification Map

Use this map when explaining the project to a reviewer or operator:

```text
Harbor/RL job context
    |
    +--> local proof path
    |       mock worker -> verifier signature -> hook simulator ledger payout -> replay rejection
    |
    +--> on-chain proof path
    |       ClaimV1 -> settle_claim -> Token-2022 transfer_checked CPI -> TransferGuard hook
    |       SlashClaimV1 -> slash_operator -> Token-2022 transfer_checked CPI -> treasury
    |
    +--> hardware proof path
            GHCR EIF -> EC2 Nitro Enclave -> NSM attestation -> AWS root verification
            -> PCR16 equals registry ClaimV1 PCR16
```

What this proves today:

- The local protocol pays once and rejects replay.
- The registry compiles the Token-2022 CPI paths and rejects common drift through lints and instruction tests.
- Real AWS Nitro produces the attestation, PCRs, and PCR16 bridge from an immutable commit-tagged EIF.

What it does not yet prove:

- A single local-validator transaction where Token-2022 invokes the hook and token balances change.
- Production Harbor-over-Nitro execution over VSOCK RPC.
- A persistent verifier enclave that ingests real AWS COSE attestations and signs on-chain claims.

Those are implementation plan items, not claims to make in public verification docs.
