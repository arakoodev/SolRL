from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from solrl_core.config import load_config
from solrl_core.mock_worker import build_claim_context, build_mock_artifacts, build_mock_attestation
from solrl_core.verifier_service import SolrlVerifierServer


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_verifier_service_signs_mock_attestation(tmp_path: Path) -> None:
    config = load_config()
    artifacts = build_mock_artifacts(tmp_path, config["job"]["job_account"])
    claim_context = build_claim_context(config, artifacts, "service-nonce")
    attestation = build_mock_attestation(config, claim_context)

    server = SolrlVerifierServer(("127.0.0.1", 0), Path("solrl.toml"), "solrl-local-verifier")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, body = _post_json(
            f"http://127.0.0.1:{port}/verify/mock",
            {"attestation": attestation, "claim_context": claim_context},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert body["claim"]["pcr16"] == claim_context["pcr16"]
    assert body["claim_hash"]
    assert body["signature"]


def test_verifier_service_rejects_unbound_claim_context(tmp_path: Path) -> None:
    config = load_config()
    artifacts = build_mock_artifacts(tmp_path, config["job"]["job_account"])
    claim_context = build_claim_context(config, artifacts, "service-nonce")
    attestation = build_mock_attestation(config, claim_context)
    claim_context["amount"] += 1

    server = SolrlVerifierServer(("127.0.0.1", 0), Path("solrl.toml"), "solrl-local-verifier")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, body = _post_json(
            f"http://127.0.0.1:{port}/verify/mock",
            {"attestation": attestation, "claim_context": claim_context},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 400
    assert "claim context hash" in body["error"]
