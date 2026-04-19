from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from solrl_core.claim import load_json, write_json
from solrl_core.config import load_config
from solrl_core.mock_hook import HookError, apply_transfer_hook
from solrl_core.mock_verifier import verify_and_sign
from solrl_core.mock_worker import run as run_mock_worker


def _run_local_mock(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    work_dir: Path = args.work_dir
    state_path: Path = args.state
    work_dir.mkdir(parents=True, exist_ok=True)

    run_mock_worker(args.config, work_dir, args.nonce)
    attestation = load_json(work_dir / "attestation.json")
    claim_context = load_json(work_dir / "claim_context.json")
    receipt = verify_and_sign(config, attestation, claim_context, args.seed)
    write_json(work_dir / "claim_receipt.json", receipt)

    state = {"consumed_nonces": [], "ledger": []}
    updated = apply_transfer_hook(config, receipt, state, args.seed)
    write_json(state_path, updated)

    replay_rejected = False
    try:
        apply_transfer_hook(config, receipt, updated, args.seed)
    except HookError:
        replay_rejected = True
    if not replay_rejected:
        raise HookError("replay was accepted")

    summary = {
        "status": "paid",
        "amount": receipt["claim"]["amount"],
        "claim_hash": receipt["claim_hash"],
        "pcr16": receipt["claim"]["pcr16"],
        "replay_rejected": True,
        "state": str(state_path),
        "work_dir": str(work_dir),
    }
    write_json(work_dir / "mvp_result.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SolRL developer command surface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    local_mock = subparsers.add_parser(
        "local-mock",
        description="Run the local worker -> verifier -> hook simulator flow and prove replay rejection",
    )
    local_mock.add_argument("--config", type=Path, default=Path("solrl.toml"))
    local_mock.add_argument("--work-dir", type=Path, default=Path("artifacts/mock"))
    local_mock.add_argument("--state", type=Path, default=Path("artifacts/mock/hook_state.json"))
    local_mock.add_argument("--nonce", default="local-attempt-1")
    local_mock.add_argument("--seed", default="solrl-local-verifier")
    local_mock.set_defaults(func=_run_local_mock)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
