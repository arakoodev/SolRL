# SolRL Troubleshooting

## Docker Compose

If a script says it must run inside Docker, do not bypass it. Run the matching Compose command.

If LocalStack tests flake after Compose edits, restart it:

```bash
docker compose down
docker compose up -d localstack
```

Do not run multiple Compose commands that depend on LocalStack in parallel right after changing `docker-compose.yml`.

## Lint Warnings

Anchor macro `unexpected cfg` warnings can appear under the Solana/Anchor toolchain. Treat new hard failures as real, but do not churn code just to silence upstream macro warnings unless the warning becomes actionable.

## Real AWS Nitro Smoke

If the runner fails before launch:

1. Check `.env` exists locally and is not committed.
2. Run the project audit command.
3. Confirm no active/stopped `Project=SolRL` resources are left.

If launch succeeds but no final result block appears:

1. Read `artifacts/aws-nitro/<run-id>/console-output.txt`.
2. Check the last `SOLRL_PHASE_START`, `SOLRL_PHASE_END`, or `SOLRL_PHASE_FAILED`.
3. If the last phase is `pull_eif`, check `.github/workflows/build-nitro-eif.yml` is green for the exact commit and that
   `ghcr.io/<owner>/solrl-nitro-worker-eif:<commit-sha>` is public.
4. Run the audit command before any cleanup.
5. If cleanup is needed, use exact run-id cleanup only.

Why the current path works:

```text
cloud-init phase markers -> instance stops -> local runner fetches one final console block
```

The runner no longer streams build artifacts through EC2 console output. GitHub Actions builds the EIF, publishes it to
GHCR, and the EC2 parent pulls it with ORAS. The console only carries small phase markers plus one final result block.

What already failed:

- Small phase markers.
- Final-only result blocks.
- `/dev/console`.
- `/dev/ttyS0`.
- Sleep before shutdown.
- Capped log tails.
- Chunking large build JSON or attestation blobs through the 65KB console tail.

Do not bring back tail hacks or chunk protocols. If this path needs richer artifacts, propose a real scoped return
channel as an explicit architecture decision.

## Verification Problems

If a reviewer asks "where is the token used?", do not point at the Nitro smoke. Point at:

- `artifacts/mock/hook_state.json` for the local payout ledger and replay rejection.
- `programs/solrl-registry/src/lib.rs` for `settle_claim`, `slash_operator`, and Token-2022 `transfer_checked` CPI.
- `programs/solrl-registry/tests/registry_flow.rs` for the local-validator Token-2022 balance test.
- `scripts/check-token2022-wiring.py` for the lint that prevents fake flag-only settlement.
- `README.md` section `Verification` for the exact proof commands.

If someone asks whether the transfer hook fires during settlement, say no. V1 settlement uses registry PDA authorities
over escrow and stake vaults because Solana rejects same-program `registry -> Token-2022 -> registry hook` reentry. The
live balance proof is `settle_claim_transfers_token2022_balance_with_registry_pda_authority`.

## AWS Cleanup

Use:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner cleanup --run-id <run-id>
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
```

If tags do not exactly match, stop. Manual AWS console inspection is safer than clever code here.
