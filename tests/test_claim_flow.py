from __future__ import annotations

from pathlib import Path

import pytest

from solrl_core.claim import (
    claim_hash,
    claim_message,
    hex32,
    load_json,
    private_key_from_seed,
    sign_slash_claim,
    slash_claim_hash,
    sign_claim,
    verifier_identity,
    verify_claim,
    verify_slash_claim,
)
from solrl_core.config import load_config
from solrl_core.mock_hook import HookError, apply_transfer_hook
from solrl_core.mock_verifier import verify_and_sign
from solrl_core.mock_worker import build_claim_context, build_mock_artifacts, build_mock_attestation


def _receipt(tmp_path):
    config = load_config()
    artifacts = build_mock_artifacts(tmp_path, config["job"]["job_account"])
    claim_context = build_claim_context(config, artifacts, "pytest-nonce")
    attestation = build_mock_attestation(config, claim_context)
    return config, verify_and_sign(config, attestation, claim_context, "solrl-local-verifier")


def test_claim_v1_golden_vector_matches_python_encoder():
    fixture = load_json(Path("tests/fixtures/claim_v1_golden.json"))

    assert claim_hash(fixture["claim"]) == fixture["claim_hash"]
    assert claim_message(fixture["claim"]).hex() == fixture["claim_message_hex"]


def test_claim_signature_roundtrip(tmp_path):
    _config, receipt = _receipt(tmp_path)
    claim = receipt["claim"]
    identity = verifier_identity("solrl-local-verifier")
    signature = sign_claim(claim, private_key_from_seed("solrl-local-verifier"))

    assert receipt["claim_hash"] == claim_hash(claim)
    assert identity.public_key_hex == receipt["verifier_public_key"]
    assert verify_claim(claim, signature, identity.public_key_hex)
    assert "network_policy_hash" in claim
    assert len(bytes.fromhex(claim["pcr16"])) == 48


def test_hook_accepts_once_and_rejects_replay(tmp_path):
    config, receipt = _receipt(tmp_path)
    state = {"consumed_nonces": [], "ledger": []}

    updated = apply_transfer_hook(config, receipt, state, "solrl-local-verifier")

    assert updated["ledger"][0]["amount"] == config["job"]["amount"]
    with pytest.raises(HookError, match="replay"):
        apply_transfer_hook(config, receipt, updated, "solrl-local-verifier")


def test_hook_rejects_wrong_verifier(tmp_path):
    config, receipt = _receipt(tmp_path)

    with pytest.raises(HookError, match="verifier key"):
        apply_transfer_hook(config, receipt, {"consumed_nonces": [], "ledger": []}, "wrong-seed")


def test_hook_rejects_wrong_cluster(tmp_path):
    config, receipt = _receipt(tmp_path)
    receipt["claim"]["cluster_hash"] = hex32("other-cluster")
    receipt["claim_hash"] = claim_hash(receipt["claim"])
    receipt["signature"] = sign_claim(
        receipt["claim"],
        private_key_from_seed("solrl-local-verifier"),
    )

    with pytest.raises(HookError, match="cluster"):
        apply_transfer_hook(config, receipt, {"consumed_nonces": [], "ledger": []}, "solrl-local-verifier")


def test_slash_claim_signature_roundtrip(tmp_path):
    config, receipt = _receipt(tmp_path)
    identity = verifier_identity("solrl-local-verifier")
    slash_claim = {
        "cluster_hash": hex32(config["protocol"]["cluster_id"]),
        "program_id": config["protocol"]["program_id"],
        "token_mint": config["protocol"]["token_mint"],
        "operator_account": config["operator"]["operator_account"],
        "stake_token_account": config["operator"]["stake_token_account"],
        "treasury_token_account": config["protocol"]["treasury_token_account"],
        "verifier_policy_id": hex32(config["verifier_policy"]["policy_id"]),
        "verifier_id": hex32("local-verifier"),
        "claim_hash": receipt["claim_hash"],
        "lease_account": config["job"]["lease_account"],
        "job_account": config["job"]["job_account"],
        "reason_code": 3,
        "slash_amount": 25,
        "evidence_hash": hex32("wrong-job-evidence"),
        "nonce": hex32("slash-nonce"),
        "expires_unix": config["job"]["claim_expiry_unix"],
        "protocol_version": config["protocol"]["protocol_version"],
    }
    signature = sign_slash_claim(slash_claim, private_key_from_seed("solrl-local-verifier"))

    assert len(slash_claim_hash(slash_claim)) == 64
    assert verify_slash_claim(slash_claim, signature, identity.public_key_hex)
