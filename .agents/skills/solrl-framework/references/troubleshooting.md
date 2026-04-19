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
3. Run the audit command before any cleanup.
4. If cleanup is needed, use exact run-id cleanup only.

Known limitation:

```text
EC2 GetConsoleOutput is not reliable as the only completion/result transport for long Nitro smoke runs.
```

We already tried:

- Small phase markers.
- Final-only result blocks.
- `/dev/console`.
- `/dev/ttyS0`.
- Sleep before shutdown.
- Capped log tails.

Do not keep inventing tail hacks. Use a real, scoped return channel after user approval.

## AWS Cleanup

Use:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner cleanup --run-id <run-id>
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
```

If tags do not exactly match, stop. Manual AWS console inspection is safer than clever code here.
