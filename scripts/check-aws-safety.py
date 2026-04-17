#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "python/solrl_core/aws_nitro_runner.py"
E2E = ROOT / "scripts/e2e-aws-nitro.sh"
SIDECAR = ROOT / "scripts/aws-nitro-smoke.sh"


def fail(message: str) -> None:
    print(f"aws safety lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(text: str, needle: str, message: str) -> None:
    if needle not in text:
        fail(message)


def main() -> int:
    if SIDECAR.exists():
        fail("scripts/aws-nitro-smoke.sh must not exist; AWS smoke must use the main runner path")

    runner = RUNNER.read_text(encoding="utf-8")
    e2e = E2E.read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    require(e2e, "python3 -m solrl_core.aws_nitro_runner smoke", "e2e-aws-nitro.sh must call the main runner module")
    require(runner, "def require_docker(", "AWS runner must refuse host execution")
    require(runner, "SOLRL_IN_DOCKER", "AWS runner must check SOLRL_IN_DOCKER")
    require(runner, "Project", "AWS runner must define Project tag")
    require(runner, "SolRLRunId", "AWS runner must define run-id tag")
    require(runner, "ManagedBy", "AWS runner must define ManagedBy tag")
    require(runner, "TagSpecifications=tag_specifications", "EC2 resources must use tag specifications")
    require(runner, "tags_match(", "cleanup must be gated on ownership tags")
    require(runner, "EnclaveOptions={\"Enabled\": True}", "EC2 parent must launch with Nitro Enclaves enabled")
    require(runner, "AssociatePublicIpAddress", "runner must make egress explicit for default subnets")
    require(runner, "SOLRL_NITRO_AMI_ID", "AWS runner must allow an explicit AMI override")
    require(runner, "describe_images(", "AWS runner must resolve the AMI through ec2:DescribeImages")
    require(runner, "UserData=user_data", "AWS runner must execute the remote smoke through EC2 user-data")
    require(runner, "get_console_output(", "AWS runner must collect smoke markers through EC2 console output")
    require(
        runner,
        "cat >/etc/nitro_enclaves/allocator.yaml",
        "AWS runner must write Nitro allocator config in the main user-data path",
    )
    require(
        runner,
        "systemctl daemon-reload",
        "AWS runner must reload systemd before restarting the Nitro allocator",
    )
    require(
        runner,
        "journalctl -u nitro-enclaves-allocator.service",
        "AWS runner must dump allocator journal on remote smoke failure",
    )
    require(
        runner,
        "NITRO_CLI_ARTIFACTS",
        "AWS runner must set Nitro CLI artifacts path before build-enclave",
    )
    require(runner, "NITRO_CLI_BLOBS", "AWS runner must set Nitro CLI blobs path before build-enclave")
    require(
        runner,
        "Request::ExtendPCR {{ index: 16",
        "AWS runner must bind ClaimV1 context into PCR16 before attestation",
    )
    require(
        runner,
        "Request::LockPCR {{ index: 16",
        "AWS runner must lock PCR16; unlocked PCRs are not included in Nitro attestations",
    )
    require(
        runner,
        "Request::DescribePCR {{ index: 16",
        "AWS runner must verify PCR16 is locked before requesting attestation",
    )
    require(
        runner,
        "SOLRL_ATTESTATION_HEX_CHUNK",
        "AWS runner must chunk attestation output; EC2 console corrupts long marker lines",
    )
    require(
        runner,
        "SOLRL_ATTESTATION_HEX_WIDTH",
        "AWS runner must publish chunk width so corrupted console chunks can be rejected",
    )
    require(
        runner,
        "SOLRL_ATTESTATION_HEX_CHUNKS",
        "AWS runner must count attestation chunks so console interleaving cannot silently truncate output",
    )
    require(
        runner,
        "SOLRL_ATTESTATION_HEX_PASS",
        "AWS runner must repeat chunk output because EC2 console lines can interleave with cloud-init noise",
    )
    require(
        runner,
        'fold -w "$ATTESTATION_CHUNK_WIDTH"',
        "AWS runner must use one configured attestation chunk width everywhere",
    )
    require(runner, "%04d:%s", "AWS runner must index attestation chunks")
    if "systemctl enable --now nitro-enclaves-allocator.service" in runner:
        fail("Nitro allocator must not be started before allocator.yaml exists")
    if 'echo SOLRL_ATTESTATION_HEX="$(cat /tmp/solrl-attestation.hex)"' in runner:
        fail("AWS runner must not emit the attestation as one long EC2 console line")

    if "authorize_security_group_ingress" in runner:
        fail("AWS runner must not add inbound security group rules")
    if "KeyName" in runner:
        fail("AWS runner must not create or use SSH key pairs")
    if "--debug-mode" in runner:
        fail("AWS runner must not use debug-mode for attestation smoke")
    for forbidden in (
        "client(\"iam\")",
        "client('iam')",
        "create_role",
        "create_instance_profile",
        "IamInstanceProfile",
        "PassRole",
        "send_command",
        "get_command_invocation",
        "describe_instance_information",
    ):
        if forbidden in runner:
            fail(f"AWS runner default path must not depend on IAM or SSM: found {forbidden}")
    if "env_file:" in compose and ".env" in compose:
        fail("docker-compose.yml must not load .env through env_file; the runner reads it without exposing secrets")
    if "dt.UTC" in runner:
        fail("AWS runner must stay Python 3.10-compatible inside dev-shell; use dt.timezone.utc")

    print("aws safety lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
