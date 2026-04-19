from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from solrl_core.config import load_config
from solrl_core.mock_verifier import VerificationError, verify_and_sign


class VerifierServiceError(RuntimeError):
    pass


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = handler.headers.get("Content-Length")
    if content_length is None:
        raise VerifierServiceError("missing Content-Length")
    try:
        length = int(content_length)
    except ValueError as exc:
        raise VerifierServiceError("invalid Content-Length") from exc
    if length <= 0:
        raise VerifierServiceError("empty request body")
    raw = handler.rfile.read(length)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerifierServiceError("request body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise VerifierServiceError("request body must be a JSON object")
    return body


class SolrlVerifierHandler(BaseHTTPRequestHandler):
    server: "SolrlVerifierServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            _json_response(self, HTTPStatus.OK, {"ok": True, "service": "solrl-verifier"})
            return
        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/verify/mock":
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            body = _read_json(self)
            attestation = body["attestation"]
            claim_context = body["claim_context"]
            if not isinstance(attestation, dict) or not isinstance(claim_context, dict):
                raise VerifierServiceError("attestation and claim_context must be JSON objects")
            receipt = verify_and_sign(
                self.server.solrl_config,
                attestation,
                claim_context,
                self.server.verifier_seed,
            )
        except KeyError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"missing field: {exc.args[0]}"})
            return
        except (VerifierServiceError, VerificationError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        _json_response(self, HTTPStatus.OK, receipt)


class SolrlVerifierServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config_path: Path,
        verifier_seed: str,
        verbose: bool = False,
    ) -> None:
        super().__init__(server_address, SolrlVerifierHandler)
        self.solrl_config = load_config(config_path)
        self.verifier_seed = verifier_seed
        self.verbose = verbose


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SolRL mock verifier as a small HTTP service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--config", type=Path, default=Path("solrl.toml"))
    parser.add_argument("--seed", default="solrl-local-verifier")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    server = SolrlVerifierServer((args.host, args.port), args.config, args.seed, args.verbose)
    print(f"solrl verifier service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
