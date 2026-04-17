from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from solrl_core.claim import (
    claim_hash,
    hex32,
    private_key_from_seed,
    sha256_hex,
    sign_claim,
    verifier_identity,
    write_json,
)
from solrl_core.config import load_config
from solrl_core.claim import load_json


class VerificationError(RuntimeError):
    pass


def _attestation_hash(attestation: dict[str, Any]) -> str:
    return sha256_hex(attestation)


def verify_and_sign(
    config: dict[str, Any],
    attestation: dict[str, Any],
    claim_context: dict[str, Any],
    seed: str,
) -> dict[str, Any]:
    if attestation.get("kind") != "mock-nitro-attestation-v1":
        raise VerificationError("unsupported attestation kind")
    expected_pcrs = config["image_policy"]
    pcrs = attestation["pcrs"]
    for source_key, config_key in (("0", "pcr0"), ("1", "pcr1"), ("2", "pcr2")):
        if pcrs[source_key] != expected_pcrs[config_key]:
            raise VerificationError(f"PCR{source_key} mismatch")
    if len(pcrs["16"]) != 96:
        raise VerificationError("PCR16 must be a 48-byte hex string")
    if pcrs["16"] != claim_context["pcr16"]:
        raise VerificationError("PCR16 mismatch")
    if attestation.get("public_key_hash") != claim_context["worker_public_key_hash"]:
        raise VerificationError("worker public key hash mismatch")
    context_hash = sha256_hex(claim_context)
    if attestation["user_data"]["claim_context_hash"] != context_hash:
        raise VerificationError("claim context hash not bound in attestation user_data")

    claim = dict(claim_context)
    claim["attestation_document_hash"] = _attestation_hash(attestation)
    identity = verifier_identity(seed)
    signature = sign_claim(claim, private_key_from_seed(seed))
    return {
        "claim": claim,
        "claim_hash": claim_hash(claim),
        "signature": signature,
        "verifier_public_key": identity.public_key_hex,
        "verifier_policy_id": hex32(config["verifier_policy"]["policy_id"]),
        "image_policy_id": hex32(config["image_policy"]["policy_id"]),
    }


def run(config_path: Path, work_dir: Path, seed: str) -> None:
    config = load_config(config_path)
    attestation = load_json(work_dir / "attestation.json")
    claim_context = load_json(work_dir / "claim_context.json")
    receipt = verify_and_sign(config, attestation, claim_context, seed)
    write_json(work_dir / "claim_receipt.json", receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify mock attestation and sign ClaimV1")
    parser.add_argument("--config", type=Path, default=Path("solrl.toml"))
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/mock"))
    parser.add_argument("--seed", default="solrl-local-verifier")
    args = parser.parse_args()
    run(args.config, args.work_dir, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
