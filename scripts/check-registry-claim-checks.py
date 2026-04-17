#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "programs/solrl-registry/src/lib.rs"

REQUIRED_VALIDATE_CLAIM_REFERENCES = (
    "cluster_hash",
    "program_id",
    "token_mint",
    "hook_program_id",
    "job_account",
    "lease_account",
    "claim_receipt_account",
    "operator_account",
    "payout_token_account",
    "amount",
    "resource_class_hash",
    "verifier_policy_id",
    "image_policy_id",
    "worker_public_key_hash",
    "task_hash",
    "reward_script_hash",
    "harbor_environment_hash",
    "artifact_policy_hash",
    "network_policy_hash",
    "attestation_document_hash",
    "trajectory_hash",
    "pcr16",
    "reward_value",
    "lease_expiry_unix",
    "claim_expiry_unix",
    "nonce",
    "protocol_version",
)

REQUIRED_ACCOUNTS = (
    "pub struct Operator",
    "pub struct Job",
    "pub struct Lease",
    "pub struct ClaimReceipt",
    "pub struct VerifierPolicy",
)


def fail(message: str) -> None:
    print(f"registry claim lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"fn {name}\([^)]*\).*?\{{", source, re.S)
    if not match:
        fail(f"missing function {name}")
    start = match.end()
    depth = 1
    index = start
    while index < len(source):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
        index += 1
    fail(f"could not parse function {name}")


def main() -> int:
    source = REGISTRY.read_text(encoding="utf-8")
    validate_claim = function_body(source, "validate_claim")

    missing_accounts = [item for item in REQUIRED_ACCOUNTS if item not in source]
    if missing_accounts:
        fail(f"missing required account structs: {', '.join(missing_accounts)}")

    missing_fields = [field for field in REQUIRED_VALIDATE_CLAIM_REFERENCES if f"claim.{field}" not in validate_claim]
    if missing_fields:
        fail(f"validate_claim does not reference signed fields: {', '.join(missing_fields)}")

    for required_error in (
        "InvalidCluster",
        "InvalidNetworkPolicyHash",
        "InvalidWorkerPublicKeyHash",
        "InvalidRewardValue",
        "Replay",
    ):
        if required_error not in source:
            fail(f"missing error taxonomy item {required_error}")

    print("registry claim lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
