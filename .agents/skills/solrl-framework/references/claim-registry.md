# Claim, PCR16, And Registry Rules

## Canonical ClaimV1

Rust and Python must encode the same bytes.

```text
crates/solrl-claim
    < must match >
python/solrl_core/claim.py
    < tested by >
tests/fixtures/claim_v1_golden.json
```

If a field is signed, it must either be:

- Cross-checked against on-chain state,
- Used to derive a PDA or receipt,
- Explicitly documented as informational and excluded from security decisions.

A signed-but-unchecked field is usually a replay bug.

## PCR16

PCR16 is not decorative. It is the hardware PCR tying the Harbor/eval context to the claim.

SolRL uses two related values:

```text
Pcr16Components
    |
    v
pcr16_user_data = sha384(domain || b"\0" || borsh(Pcr16Components))
    |
    | NSM ExtendPCR(index=16, data=pcr16_user_data)
    v
pcr16_digest = sha384(zeros48 || pcr16_user_data)
```

`pcr16_user_data` is what the Nitro worker sends as attestation `user_data`. `pcr16_digest` is the locked PCR16 value
that ClaimV1 signs and the registry recomputes. If those two values collapse into one again, the AWS proof and Solana
settlement rail drift apart. Bad.

When changing PCR16 inputs, update:

- Rust PCR16 helpers.
- Python PCR16 helpers.
- Registry cross-checks.
- Mock worker/verifier flow.
- Golden fixture and tests.
- AWS Nitro runner user-data bridge.

The registry should recompute PCR16 from registered components where possible, not trust caller-provided bytes.

## Token-2022 Settlement

Do not fake token movement by flipping flags.

The expected settlement shape is:

```text
settle_claim
    |
    +--> verify claim and policy graph
    +--> create replay receipts
    +--> arm one-use TransferGuard
    +--> invoke Token-2022 transfer_checked
    +--> transfer hook validates caller + consumes guard
```

The hook is not the whole verifier. `settle_claim` owns the full claim graph. The hook is the narrow guard that prevents arbitrary direct transfers around settlement.

## What Counts As Token Evidence

For competition evaluation, separate three levels of token evidence:

```text
local MVP evidence
    artifacts/mock/mvp_result.json       status=paid, replay_rejected=true
    artifacts/mock/hook_state.json       one ledger payout to the operator payout token account
    artifacts/mock/claim_receipt.json    ClaimV1 amount, token_mint, payout_token_account, signature

implementation evidence
    programs/solrl-registry/src/lib.rs   settle_claim and slash_operator call Token-2022 transfer_checked
    scripts/check-token2022-wiring.py    rejects fake flag-only settlement regressions
    scripts/check-registry-claim-checks.py rejects signed-but-unchecked claim fields

missing V1 evidence
    no full local-validator balance test yet proving Token-2022 invokes the hook and mutates token balances
```

Say this plainly if asked whether the token is live: the registry is built around a Token-2022 mint, escrow token account,
operator payout token account, operator stake token account, and treasury token account. `settle_claim` moves escrow to
operator payout, and `slash_operator` moves operator stake to treasury. The local MVP proves the claim-to-payout behavior
with a deterministic simulator. The remaining proof gap is a balance-level local-validator test through the real Token-2022
program hook path.

## Lints That Should Catch Past Mistakes

Run:

```bash
docker compose run --rm lint
```

Relevant checks:

- `scripts/check-claim-schema-parity.py`
- `scripts/check-registry-claim-checks.py`
- `scripts/check-token2022-wiring.py`
- `scripts/check-aws-safety.py`
- `scripts/check-docker-boundary.sh`
- `scripts/check-mvp-entrypoints.py`

When fixing a bug that should never happen again, add a lint or regression test in the same change. This repo already paid the tuition. Do not pay it twice.
