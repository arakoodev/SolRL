# AWS Nitro Safety Rules

The AWS account is shared. Assume other humans are using it.

## Before Launch

Run:

```bash
docker compose run --rm aws-nitro-runner python3 -m solrl_core.aws_nitro_runner audit --scope project
```

By default, the smoke should refuse to launch if active or stopped `Project=SolRL` resources already exist. Use `--allow-existing-solrl` only when the user explicitly wants overlapping SolRL runs.

## Resource Rules

Every created resource must carry:

```text
Project=SolRL
SolRLRunId=<exact run id>
ManagedBy=SolRL
```

EC2 parent rules:

- Nitro enclaves enabled.
- IMDSv2 required.
- No SSH key pair.
- No instance profile.
- No SSM dependency.
- No inbound security group rules.
- Root volume `DeleteOnTermination=True`.
- No debug-mode enclave.

Cleanup rules:

- Delete only exact current run resources.
- Check tags immediately before termination or deletion.
- Never delete by name prefix alone.
- Never clean all account resources.
- Post-audit must show zero active/stopped `Project=SolRL` instances, security groups, and volumes unless the user allowed overlap.

## Return Channel

The no-S3, no-IAM path currently relies on final EC2 console output. Console output is useful for small phase markers and
one final result block. It is not an artifact transport.

The EIF is built by GitHub Actions and published as a public GHCR OCI artifact. The EC2 parent pulls it with ORAS and
verifies the `.sha384` sidecar before booting it. Do not put GHCR credentials in EC2 user-data for the default smoke path.

The final result must include `SOLRL_PCR16`, `SOLRL_CLAIM_PCR16`, and `SOLRL_CLAIM_CONTEXT_HASH`. `SOLRL_PCR16` and
`SOLRL_CLAIM_PCR16` must be equal. This is the regression check that proves the real Nitro smoke is using the same PCR16
meaning as the registry.

If asked to make real AWS smoke reliable, propose a scoped return channel instead of adding more tail parsing:

- A minimal instance role that can write only this instance's exact run result tag, or
- SSM Parameter Store under `/solrl/runs/<run_id>` with a tightly scoped policy, or
- Another user-approved, exact-run, tagged output channel.

Do not sneak this in. The user previously rejected extra IAM as overkill. Bring the observed failure and tradeoff back to them first.

## Evidence To Report

After a real run, report:

- AWS account id and caller ARN.
- Run id.
- AMI, instance type, VPC/subnet, security group id.
- Tags used.
- Whether attestation verified.
- EIF OCI ref and EIF SHA-384.
- `SOLRL_PCR0`, `SOLRL_PCR1`, `SOLRL_PCR2`, and `SOLRL_PCR16`.
- Whether `SOLRL_PCR16 == SOLRL_CLAIM_PCR16`.
- Whether the instance stopped/terminated.
- Post-audit counts for SolRL instances, security groups, and volumes.

Do not use real AWS output as token evidence by itself. Nitro proves the hardware rail. Token proof comes from the local
MVP artifacts plus the registry/Token-2022 build and lint evidence.
