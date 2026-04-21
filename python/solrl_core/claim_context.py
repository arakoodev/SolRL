from __future__ import annotations

from pathlib import Path
from typing import Any

from solrl_core.claim import (
    CLAIM_FIELDS,
    hex32,
    pcr16_digest,
    pcr16_user_data,
    sha256_hex,
    write_json,
)


def build_mock_artifacts(out_dir: Path, job_id: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    trajectory = {
        "job_id": job_id,
        "steps": [
            {"cmd": "echo harbor-eval", "return_code": 0},
            {"cmd": "tests/test.sh", "return_code": 0},
        ],
        "reward": 1,
    }
    write_json(out_dir / "trajectory.json", trajectory)
    return {
        "artifact_uri": f"file://{out_dir.resolve()}",
        "trajectory_hash": sha256_hex(trajectory),
        "reward_value": 1,
    }


def build_aws_smoke_artifacts(run_id: str) -> dict[str, Any]:
    trajectory = {
        "kind": "aws-nitro-smoke",
        "run_id": run_id,
        "reward": 1,
    }
    return {
        "artifact_uri": f"aws-nitro-smoke://{run_id}",
        "trajectory_hash": sha256_hex(trajectory),
        "reward_value": 1,
    }


def build_pcr16_components(config: dict[str, Any], artifacts: dict[str, Any], nonce: str) -> dict[str, Any]:
    protocol = config["protocol"]
    job = config["job"]
    operator = config["operator"]
    artifact = config["artifact"]

    task_bundle = {
        "job_account": job["job_account"],
        "network_policy": protocol["network_policy"],
        "resource_class": job["resource_class"],
        "reward_script": "examples/harbor_task/tests/test.sh",
    }
    artifact_policy = {
        "bucket": artifact["bucket"],
        "retention_days": artifact["retention_days"],
    }
    return {
        "job_account": job["job_account"],
        "lease_account": job["lease_account"],
        "nonce": hex32(nonce),
        "task_hash": sha256_hex(task_bundle),
        "task_toml_hash": hex32("examples/harbor_task/task.toml"),
        "instruction_hash": hex32("examples/harbor_task/instruction.md"),
        "test_hash": hex32("examples/harbor_task/tests/test.sh"),
        "reward_script_hash": hex32("deterministic-test-sh-v1"),
        "harbor_environment_hash": hex32("solrl-harbor-import-path-v1"),
        "resource_class_hash": hex32(job["resource_class"]),
        "timeout_seconds": job["timeout_seconds"],
        "network_policy_hash": hex32(protocol["network_policy"]),
        "operator_account": operator["operator_account"],
        "payout_token_account": operator["payout_token_account"],
        "token_mint": protocol["token_mint"],
        "artifact_policy_hash": sha256_hex(artifact_policy),
        "protocol_version": protocol["protocol_version"],
    }


def build_claim_context(config: dict[str, Any], artifacts: dict[str, Any], nonce: str) -> dict[str, Any]:
    protocol = config["protocol"]
    job = config["job"]
    operator = config["operator"]
    image_policy = config["image_policy"]
    verifier_policy = config["verifier_policy"]
    components = build_pcr16_components(config, artifacts, nonce)
    worker_public_key = hex32("solrl-local-worker")

    return {
        "amount": job["amount"],
        "artifact_policy_hash": components["artifact_policy_hash"],
        "claim_expiry_unix": job["claim_expiry_unix"],
        "claim_receipt_account": job["claim_receipt_account"],
        "cluster_hash": hex32(protocol["cluster_id"]),
        "harbor_environment_hash": components["harbor_environment_hash"],
        "hook_program_id": protocol["hook_program_id"],
        "image_policy_id": hex32(image_policy["policy_id"]),
        "job_account": job["job_account"],
        "lease_account": job["lease_account"],
        "lease_expiry_unix": job["lease_expiry_unix"],
        "network_policy_hash": components["network_policy_hash"],
        "nonce": components["nonce"],
        "operator_account": operator["operator_account"],
        "payout_token_account": operator["payout_token_account"],
        "pcr16": pcr16_digest(components),
        "pcr16_user_data": pcr16_user_data(components),
        "program_id": protocol["program_id"],
        "protocol_version": protocol["protocol_version"],
        "resource_class_hash": components["resource_class_hash"],
        "reward_script_hash": components["reward_script_hash"],
        "reward_value": artifacts["reward_value"],
        "task_hash": components["task_hash"],
        "token_mint": protocol["token_mint"],
        "trajectory_hash": artifacts["trajectory_hash"],
        "verifier_policy_id": hex32(verifier_policy["policy_id"]),
        "worker_public_key_hash": hex32(worker_public_key),
    }


def claim_fields_from_context(claim_context: dict[str, Any]) -> dict[str, Any]:
    return {field: claim_context[field] for field in CLAIM_FIELDS if field in claim_context}


def claim_context_hash(claim_context: dict[str, Any]) -> str:
    return sha256_hex(claim_context)
