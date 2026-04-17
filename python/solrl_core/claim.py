from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


CLAIM_DOMAIN = b"SOLRL_HARBOR_CLAIM_V1_FIXED"
SLASH_CLAIM_DOMAIN = b"SOLRL_HARBOR_SLASH_CLAIM_V1_FIXED"
PCR16_DOMAIN = b"SOLRL_HARBOR_PCR16_V1_FIXED"
CLAIM_PROTOCOL_VERSION = 1

SLASH_REASON_REPLAY = 1
SLASH_REASON_MALICIOUS_DUPLICATE = 2
SLASH_REASON_WRONG_JOB = 3
SLASH_REASON_WRONG_POLICY = 4
SLASH_REASON_FORGED_CONTEXT = 5
SLASH_REASON_LEASE_ABUSE = 6

CLAIM_FIELDS = (
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

SLASH_CLAIM_FIELDS = (
    "cluster_hash",
    "program_id",
    "token_mint",
    "operator_account",
    "stake_token_account",
    "treasury_token_account",
    "verifier_policy_id",
    "verifier_id",
    "claim_hash",
    "lease_account",
    "job_account",
    "reason_code",
    "slash_amount",
    "evidence_hash",
    "nonce",
    "expires_unix",
    "protocol_version",
)

PCR16_FIELDS = (
    "job_account",
    "lease_account",
    "nonce",
    "task_hash",
    "task_toml_hash",
    "instruction_hash",
    "test_hash",
    "reward_script_hash",
    "harbor_environment_hash",
    "resource_class_hash",
    "timeout_seconds",
    "network_policy_hash",
    "operator_account",
    "payout_token_account",
    "token_mint",
    "artifact_policy_hash",
    "protocol_version",
)


class ClaimError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex(value: Any) -> str:
    return sha256_hex_bytes(canonical_json(value))


def sha384_hex(value: Any) -> str:
    return hashlib.sha384(canonical_json(value)).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def hex32(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hex48(value: str) -> str:
    return hashlib.sha384(value.encode()).hexdigest()


def normalize_claim(raw: dict[str, Any]) -> dict[str, Any]:
    claim = dict(raw)
    claim.setdefault("cluster_hash", hex32(str(claim.get("cluster_id", ""))))
    claim.setdefault("resource_class_hash", hex32(str(claim.get("resource_class", ""))))
    claim.setdefault("worker_public_key_hash", hex32(str(claim.get("worker_public_key", ""))))
    claim.setdefault("network_policy_hash", hex32(str(claim.get("network_policy", ""))))
    claim.setdefault("claim_receipt_account", claim.get("claim_intent_account"))
    claim.setdefault("protocol_version", CLAIM_PROTOCOL_VERSION)

    missing = sorted(field for field in CLAIM_FIELDS if claim.get(field) is None)
    if missing:
        raise ClaimError(f"claim missing required fields: {', '.join(missing)}")
    return claim


def claim_message(claim: dict[str, Any]) -> bytes:
    return CLAIM_DOMAIN + b"\0" + encode_claim_v1(normalize_claim(claim))


def claim_hash(claim: dict[str, Any]) -> str:
    return sha256_hex_bytes(claim_message(claim))


def slash_claim_message(claim: dict[str, Any]) -> bytes:
    return SLASH_CLAIM_DOMAIN + b"\0" + encode_slash_claim_v1(claim)


def slash_claim_hash(claim: dict[str, Any]) -> str:
    return sha256_hex_bytes(slash_claim_message(claim))


def pcr16_preimage(components: dict[str, Any]) -> bytes:
    return PCR16_DOMAIN + b"\0" + encode_pcr16_components(components)


def pcr16_digest(components: dict[str, Any]) -> str:
    return hashlib.sha384(pcr16_preimage(components)).hexdigest()


def encode_claim_v1(claim: dict[str, Any]) -> bytes:
    out = bytearray()
    out += bytes32(claim["cluster_hash"])
    out += pubkey_bytes(claim["program_id"])
    out += pubkey_bytes(claim["token_mint"])
    out += pubkey_bytes(claim["hook_program_id"])
    out += pubkey_bytes(claim["job_account"])
    out += pubkey_bytes(claim["lease_account"])
    out += pubkey_bytes(claim["claim_receipt_account"])
    out += pubkey_bytes(claim["operator_account"])
    out += pubkey_bytes(claim["payout_token_account"])
    out += u64(claim["amount"])
    out += bytes32(claim["resource_class_hash"])
    out += bytes32(claim["verifier_policy_id"])
    out += bytes32(claim["image_policy_id"])
    out += bytes32(claim["worker_public_key_hash"])
    out += bytes32(claim["task_hash"])
    out += bytes32(claim["reward_script_hash"])
    out += bytes32(claim["harbor_environment_hash"])
    out += bytes32(claim["artifact_policy_hash"])
    out += bytes32(claim["network_policy_hash"])
    out += bytes32(claim["attestation_document_hash"])
    out += bytes32(claim["trajectory_hash"])
    out += bytes48(claim["pcr16"])
    out += i64(claim["reward_value"])
    out += i64(claim["lease_expiry_unix"])
    out += i64(claim["claim_expiry_unix"])
    out += bytes32(claim["nonce"])
    out += u16(claim["protocol_version"])
    return bytes(out)


def encode_slash_claim_v1(claim: dict[str, Any]) -> bytes:
    missing = [key for key in SLASH_CLAIM_FIELDS if key not in claim]
    if missing:
        raise ClaimError(f"slash claim missing required fields: {', '.join(missing)}")

    out = bytearray()
    out += bytes32(claim["cluster_hash"])
    out += pubkey_bytes(claim["program_id"])
    out += pubkey_bytes(claim["token_mint"])
    out += pubkey_bytes(claim["operator_account"])
    out += pubkey_bytes(claim["stake_token_account"])
    out += pubkey_bytes(claim["treasury_token_account"])
    out += bytes32(claim["verifier_policy_id"])
    out += bytes32(claim["verifier_id"])
    out += bytes32(claim["claim_hash"])
    out += pubkey_bytes(claim["lease_account"])
    out += pubkey_bytes(claim["job_account"])
    out += u8(claim["reason_code"])
    out += u64(claim["slash_amount"])
    out += bytes32(claim["evidence_hash"])
    out += bytes32(claim["nonce"])
    out += i64(claim["expires_unix"])
    out += u16(claim["protocol_version"])
    return bytes(out)


def encode_pcr16_components(components: dict[str, Any]) -> bytes:
    missing = [key for key in PCR16_FIELDS if key not in components]
    if missing:
        raise ClaimError(f"PCR16 components missing required fields: {', '.join(missing)}")

    out = bytearray()
    out += pubkey_bytes(components["job_account"])
    out += pubkey_bytes(components["lease_account"])
    out += bytes32(components["nonce"])
    out += bytes32(components["task_hash"])
    out += bytes32(components["task_toml_hash"])
    out += bytes32(components["instruction_hash"])
    out += bytes32(components["test_hash"])
    out += bytes32(components["reward_script_hash"])
    out += bytes32(components["harbor_environment_hash"])
    out += bytes32(components["resource_class_hash"])
    out += u32(components["timeout_seconds"])
    out += bytes32(components["network_policy_hash"])
    out += pubkey_bytes(components["operator_account"])
    out += pubkey_bytes(components["payout_token_account"])
    out += pubkey_bytes(components["token_mint"])
    out += bytes32(components["artifact_policy_hash"])
    out += u16(components["protocol_version"])
    return bytes(out)


def u8(value: Any) -> bytes:
    return int(value).to_bytes(1, "little", signed=False)


def u16(value: Any) -> bytes:
    return int(value).to_bytes(2, "little", signed=False)


def u32(value: Any) -> bytes:
    return int(value).to_bytes(4, "little", signed=False)


def u64(value: Any) -> bytes:
    return int(value).to_bytes(8, "little", signed=False)


def i64(value: Any) -> bytes:
    return int(value).to_bytes(8, "little", signed=True)


def bytes32(value: Any) -> bytes:
    return fixed_bytes(value, 32)


def bytes48(value: Any) -> bytes:
    return fixed_bytes(value, 48)


def fixed_bytes(value: Any, length: int) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, bytearray):
        raw = bytes(value)
    elif isinstance(value, str):
        raw = bytes.fromhex(value) if _is_hex(value, length) else hashlib.sha256(value.encode()).digest()
        if length == 48 and len(raw) == 32:
            raw = hashlib.sha384(value.encode()).digest()
    else:
        raise ClaimError(f"cannot encode {value!r} as {length} bytes")

    if len(raw) != length:
        raise ClaimError(f"expected {length} bytes, got {len(raw)}")
    return raw


def pubkey_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str) and _is_hex(value, 32):
        raw = bytes.fromhex(value)
    elif isinstance(value, str):
        raw = _base58_decode(value)
        if len(raw) != 32:
            raw = hashlib.sha256(value.encode()).digest()
    else:
        raise ClaimError(f"cannot encode {value!r} as pubkey")
    if len(raw) != 32:
        raise ClaimError(f"expected pubkey to be 32 bytes, got {len(raw)}")
    return raw


def _is_hex(value: str, length: int) -> bool:
    if len(value) != length * 2:
        return False
    try:
        bytes.fromhex(value)
        return True
    except ValueError:
        return False


def _base58_decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    acc = 0
    for char in value:
        if char not in alphabet:
            return hashlib.sha256(value.encode()).digest()
        acc = acc * 58 + alphabet.index(char)
    raw = acc.to_bytes((acc.bit_length() + 7) // 8, "big") if acc else b""
    leading = len(value) - len(value.lstrip("1"))
    return b"\0" * leading + raw


def private_key_from_seed(seed: str) -> Ed25519PrivateKey:
    digest = hashlib.sha256(seed.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(digest)


def private_key_to_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        Encoding.Raw,
        PrivateFormat.Raw,
        NoEncryption(),
    ).hex()


def public_key_to_hex(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def public_key_from_hex(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


def sign_claim(claim: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    return private_key.sign(claim_message(claim)).hex()


def sign_slash_claim(claim: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    return private_key.sign(slash_claim_message(claim)).hex()


def verify_claim(
    claim: dict[str, Any],
    signature_hex: str,
    public_key_hex: str,
) -> bool:
    public_key = public_key_from_hex(public_key_hex)
    try:
        public_key.verify(bytes.fromhex(signature_hex), claim_message(claim))
        return True
    except InvalidSignature:
        return False


def verify_slash_claim(
    claim: dict[str, Any],
    signature_hex: str,
    public_key_hex: str,
) -> bool:
    public_key = public_key_from_hex(public_key_hex)
    try:
        public_key.verify(bytes.fromhex(signature_hex), slash_claim_message(claim))
        return True
    except InvalidSignature:
        return False


@dataclass(frozen=True)
class VerifierIdentity:
    seed: str
    private_key_hex: str
    public_key_hex: str


def verifier_identity(seed: str) -> VerifierIdentity:
    private_key = private_key_from_seed(seed)
    return VerifierIdentity(
        seed=seed,
        private_key_hex=private_key_to_hex(private_key),
        public_key_hex=public_key_to_hex(private_key.public_key()),
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="SolRL ClaimV1 helper")
    parser.add_argument("claim", type=Path)
    parser.add_argument("--seed", default="solrl-local-verifier")
    parser.add_argument("--slash", action="store_true")
    args = parser.parse_args()

    claim = load_json(args.claim)
    identity = verifier_identity(args.seed)
    if args.slash:
        signature = sign_slash_claim(claim, private_key_from_seed(args.seed))
        digest = slash_claim_hash(claim)
    else:
        signature = sign_claim(claim, private_key_from_seed(args.seed))
        digest = claim_hash(claim)
    out = {
        "claim_hash": digest,
        "signature": signature,
        "verifier_public_key": identity.public_key_hex,
    }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
