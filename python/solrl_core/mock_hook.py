from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from solrl_core.claim import claim_hash, hex32, load_json, verifier_identity, verify_claim, write_json
from solrl_core.config import load_config


class HookError(RuntimeError):
    pass


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"consumed_nonces": [], "ledger": []}
    return load_json(path)


def apply_transfer_hook(
    config: dict[str, Any],
    receipt: dict[str, Any],
    state: dict[str, Any],
    verifier_seed: str,
) -> dict[str, Any]:
    claim = receipt["claim"]
    expected_verifier = verifier_identity(verifier_seed).public_key_hex
    if receipt["verifier_public_key"] != expected_verifier:
        raise HookError("verifier key is not active")
    if not verify_claim(claim, receipt["signature"], receipt["verifier_public_key"]):
        raise HookError("verifier signature invalid")
    if receipt["claim_hash"] != claim_hash(claim):
        raise HookError("claim hash mismatch")
    if claim["nonce"] in state["consumed_nonces"]:
        raise HookError("replay detected")
    if claim["cluster_hash"] != hex32(config["protocol"]["cluster_id"]):
        raise HookError("cluster mismatch")
    if claim["token_mint"] != config["protocol"]["token_mint"]:
        raise HookError("token mint mismatch")
    if claim["job_account"] != config["job"]["job_account"]:
        raise HookError("job mismatch")
    if claim["lease_account"] != config["job"]["lease_account"]:
        raise HookError("lease mismatch")
    if claim["claim_receipt_account"] != config["job"]["claim_receipt_account"]:
        raise HookError("claim receipt mismatch")
    if claim["amount"] != config["job"]["amount"]:
        raise HookError("amount mismatch")
    if claim["image_policy_id"] != hex32(config["image_policy"]["policy_id"]):
        raise HookError("image policy mismatch")
    if claim["verifier_policy_id"] != hex32(config["verifier_policy"]["policy_id"]):
        raise HookError("verifier policy mismatch")
    if claim["network_policy_hash"] != hex32(config["protocol"]["network_policy"]):
        raise HookError("network policy mismatch")
    now = int(time.time())
    if claim["claim_expiry_unix"] < now:
        raise HookError("claim expired")

    state["consumed_nonces"].append(claim["nonce"])
    state["ledger"].append(
        {
            "amount": claim["amount"],
            "claim_hash": receipt["claim_hash"],
            "job_account": claim["job_account"],
            "recipient": claim["payout_token_account"],
        }
    )
    return state


def run(config_path: Path, work_dir: Path, state_path: Path, verifier_seed: str) -> None:
    config = load_config(config_path)
    receipt = load_json(work_dir / "claim_receipt.json")
    state = _load_state(state_path)
    updated = apply_transfer_hook(config, receipt, state, verifier_seed)
    write_json(state_path, updated)
    write_json(work_dir / "hook_result.json", {"status": "paid", "state": updated})


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate Token-2022 transfer hook claim validation")
    parser.add_argument("--config", type=Path, default=Path("solrl.toml"))
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/mock"))
    parser.add_argument("--state", type=Path, default=Path("artifacts/mock/hook_state.json"))
    parser.add_argument("--seed", default="solrl-local-verifier")
    args = parser.parse_args()
    run(args.config, args.work_dir, args.state, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
