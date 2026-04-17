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
    require(runner, "Tags=resource_tags(self.config)", "IAM role/profile creation must include tags")
    require(runner, "TagSpecifications=tag_specifications", "EC2 resources must use tag specifications")
    require(runner, "list_instance_profile_tags", "instance profile cleanup must verify tags")
    require(runner, "tags_match(", "cleanup must be gated on ownership tags")
    require(runner, "EnclaveOptions={\"Enabled\": True}", "EC2 parent must launch with Nitro Enclaves enabled")
    require(runner, "AssociatePublicIpAddress", "runner must make egress explicit for default subnets")
    require(runner, "SOLRL_NITRO_AMI_ID", "AWS runner must allow an explicit AMI override")
    require(
        runner,
        "SOLRL_NITRO_INSTANCE_PROFILE_NAME",
        "AWS runner must support existing instance profiles for locked-down accounts",
    )
    require(runner, "describe_images(", "AWS runner must fall back when SSM public AMI parameter access is denied")

    if "authorize_security_group_ingress" in runner:
        fail("AWS runner must not add inbound security group rules")
    if "KeyName" in runner:
        fail("AWS runner must not create or use SSH key pairs")
    if "--debug-mode" in runner:
        fail("AWS runner must not use debug-mode for attestation smoke")
    if "delete_instance_profile" in runner and "_profile_tags_match()" not in runner:
        fail("instance profile deletion must be behind _profile_tags_match")
    if "env_file:" in compose and ".env" in compose:
        fail("docker-compose.yml must not load .env through env_file; the runner reads it without exposing secrets")
    if "dt.UTC" in runner:
        fail("AWS runner must stay Python 3.10-compatible inside dev-shell; use dt.timezone.utc")

    print("aws safety lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
