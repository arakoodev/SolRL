from __future__ import annotations

import hashlib
from typing import Any

from solrl_core.claim import sha256_hex


COMPUTE_DOMAIN = b"SOLRL_GENERIC_COMPUTE_V1"


def compute_input_for_run(run_id: str) -> bytes:
    return f"solrl-generic-compute:{run_id}".encode("utf-8")


def compute_output_hash(input_bytes: bytes, nonce: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(COMPUTE_DOMAIN)
    digest.update(b"\0")
    digest.update(input_bytes)
    digest.update(b"\0")
    digest.update(nonce)
    return digest.hexdigest()


def build_compute_artifacts(run_id: str, nonce_hex: str) -> dict[str, Any]:
    input_bytes = compute_input_for_run(run_id)
    nonce = bytes.fromhex(nonce_hex)
    output_hash = compute_output_hash(input_bytes, nonce)
    result = {
        "domain": COMPUTE_DOMAIN.decode("ascii"),
        "input_hash": hashlib.sha256(input_bytes).hexdigest(),
        "nonce": nonce_hex,
        "output_hash": output_hash,
        "reward_value": 1,
    }
    return {
        "artifact_uri": f"generic-compute://{run_id}",
        "compute_input_hex": input_bytes.hex(),
        "compute_input_hash": result["input_hash"],
        "compute_output_hash": output_hash,
        "trajectory_hash": output_hash,
        "compute_result_hash": sha256_hex(result),
        "reward_value": 1,
        "result": result,
    }
