from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from solrl_core.claim import hex32, pcr16_digest, sha256_hex, write_json
from solrl_core.config import load_config


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


def build_claim_context(config: dict[str, Any], artifacts: dict[str, Any], nonce: str) -> dict[str, Any]:
    protocol = config["protocol"]
    job = config["job"]
    operator = config["operator"]
    image_policy = config["image_policy"]
    verifier_policy = config["verifier_policy"]
    artifact = config["artifact"]
    worker_public_key = hex32("solrl-local-worker")

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
    network_policy_hash = hex32(protocol["network_policy"])
    resource_class_hash = hex32(job["resource_class"])
    reward_script_hash = hex32("deterministic-test-sh-v1")
    harbor_environment_hash = hex32("solrl-harbor-import-path-v1")
    task_toml_hash = hex32("examples/harbor_task/task.toml")
    instruction_hash = hex32("examples/harbor_task/instruction.md")
    test_hash = hex32("examples/harbor_task/tests/test.sh")
    task_hash = sha256_hex(task_bundle)
    artifact_policy_hash = sha256_hex(artifact_policy)
    nonce_hash = hex32(nonce)
    pcr16 = pcr16_digest(
        {
            "job_account": job["job_account"],
            "lease_account": job["lease_account"],
            "nonce": nonce_hash,
            "task_hash": task_hash,
            "task_toml_hash": task_toml_hash,
            "instruction_hash": instruction_hash,
            "test_hash": test_hash,
            "reward_script_hash": reward_script_hash,
            "harbor_environment_hash": harbor_environment_hash,
            "resource_class_hash": resource_class_hash,
            "timeout_seconds": job["timeout_seconds"],
            "network_policy_hash": network_policy_hash,
            "operator_account": operator["operator_account"],
            "payout_token_account": operator["payout_token_account"],
            "token_mint": protocol["token_mint"],
            "artifact_policy_hash": artifact_policy_hash,
            "protocol_version": protocol["protocol_version"],
        }
    )

    return {
        "amount": job["amount"],
        "artifact_policy_hash": artifact_policy_hash,
        "claim_expiry_unix": job["claim_expiry_unix"],
        "claim_receipt_account": job["claim_receipt_account"],
        "cluster_hash": hex32(protocol["cluster_id"]),
        "harbor_environment_hash": harbor_environment_hash,
        "hook_program_id": protocol["hook_program_id"],
        "image_policy_id": hex32(image_policy["policy_id"]),
        "job_account": job["job_account"],
        "lease_account": job["lease_account"],
        "lease_expiry_unix": job["lease_expiry_unix"],
        "network_policy_hash": network_policy_hash,
        "nonce": nonce_hash,
        "operator_account": operator["operator_account"],
        "payout_token_account": operator["payout_token_account"],
        "pcr16": pcr16,
        "program_id": protocol["program_id"],
        "protocol_version": protocol["protocol_version"],
        "resource_class_hash": resource_class_hash,
        "reward_script_hash": reward_script_hash,
        "reward_value": artifacts["reward_value"],
        "task_hash": task_hash,
        "token_mint": protocol["token_mint"],
        "trajectory_hash": artifacts["trajectory_hash"],
        "verifier_policy_id": hex32(verifier_policy["policy_id"]),
        "worker_public_key_hash": hex32(worker_public_key),
    }


def build_mock_attestation(config: dict[str, Any], claim_context: dict[str, Any]) -> dict[str, Any]:
    pcrs = {
        "0": config["image_policy"]["pcr0"],
        "1": config["image_policy"]["pcr1"],
        "2": config["image_policy"]["pcr2"],
        "16": claim_context["pcr16"],
    }
    return {
        "kind": "mock-nitro-attestation-v1",
        "pcrs": pcrs,
        "public_key_hash": claim_context["worker_public_key_hash"],
        "user_data": {
            "claim_context_hash": sha256_hex(claim_context),
        },
    }


def run(config_path: Path, out_dir: Path, nonce: str) -> None:
    config = load_config(config_path)
    artifacts = build_mock_artifacts(out_dir, config["job"]["job_account"])
    claim_context = build_claim_context(config, artifacts, nonce)
    attestation = build_mock_attestation(config, claim_context)
    write_json(out_dir / "claim_context.json", claim_context)
    write_json(out_dir / "attestation.json", attestation)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a mock SolRL worker attestation")
    parser.add_argument("--config", type=Path, default=Path("solrl.toml"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/mock"))
    parser.add_argument("--nonce", default="local-attempt-1")
    args = parser.parse_args()
    run(args.config, args.out, args.nonce)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
