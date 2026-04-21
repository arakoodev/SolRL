from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from solrl_core.claim import write_json
from solrl_core.claim_context import (
    build_claim_context,
    build_mock_artifacts,
    claim_context_hash,
)
from solrl_core.config import load_config


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
            "claim_context_hash": claim_context_hash(claim_context),
            "pcr16_user_data": claim_context["pcr16_user_data"],
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
