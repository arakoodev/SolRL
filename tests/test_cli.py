from __future__ import annotations

from pathlib import Path

from solrl_core.claim import load_json
from solrl_core.cli import main


def test_local_mock_cli_runs_full_mock_payment_flow(tmp_path: Path, capsys) -> None:
    work_dir = tmp_path / "mock"
    state_path = work_dir / "hook_state.json"

    result = main(
        [
            "local-mock",
            "--config",
            "solrl.toml",
            "--work-dir",
            str(work_dir),
            "--state",
            str(state_path),
            "--nonce",
            "cli-nonce",
        ]
    )

    assert result == 0
    state = load_json(state_path)
    summary = load_json(work_dir / "mvp_result.json")
    assert len(state["ledger"]) == 1
    assert summary["status"] == "paid"
    assert summary["replay_rejected"] is True
    assert "claim_hash" in capsys.readouterr().out
