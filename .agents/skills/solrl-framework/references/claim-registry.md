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

PCR16 is not decorative. It is the digest tying the Harbor/eval context to the claim.

When changing PCR16 inputs, update:

- Rust PCR16 helpers.
- Python PCR16 helpers.
- Registry cross-checks.
- Mock worker/verifier flow.
- Golden fixture and tests.

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

When fixing a bug that should never happen again, add a lint or regression test in the same change. This repo already paid the tuition. Do not pay it twice.
