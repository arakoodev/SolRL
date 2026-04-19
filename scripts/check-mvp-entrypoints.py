#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"MVP entrypoint lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(path: Path, needle: str, message: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        fail(message)


def main() -> int:
    cli = ROOT / "python/solrl_core/cli.py"
    verifier_service = ROOT / "python/solrl_core/verifier_service.py"
    harbor_env = ROOT / "python/solrl_harbor/nitro_environment.py"
    e2e_local = ROOT / "scripts/e2e-local-mock.sh"
    compose = ROOT / "docker-compose.yml"
    readme = ROOT / "README.md"
    plan = ROOT / "PLAN.md"

    for path in (cli, verifier_service, harbor_env):
        if not path.exists():
            fail(f"missing first-class MVP module: {path.relative_to(ROOT)}")

    require(cli, '"local-mock"', "CLI must expose local-mock as the canonical local demo entrypoint")
    require(cli, "replay_rejected", "CLI local-mock must prove replay rejection")
    require(e2e_local, "python3 -m solrl_core.cli local-mock", "e2e-local-mock.sh must use the main CLI path")
    for forbidden in (
        "python3 -m solrl_core.mock_worker",
        "python3 -m solrl_core.mock_verifier",
        "python3 -m solrl_core.mock_hook",
    ):
        if forbidden in e2e_local.read_text(encoding="utf-8"):
            fail(f"e2e-local-mock.sh must not reassemble the flow by hand: found {forbidden}")

    require(compose, "verifier-service:", "docker-compose.yml must expose verifier-service")
    require(
        compose,
        "python -m solrl_core.verifier_service",
        "verifier-service must run the first-class verifier module",
    )
    require(
        harbor_env,
        "solrl_harbor.nitro_environment:NitroEnvironment",
        "Harbor environment module must document its import path",
    )
    require(
        readme,
        "solrl_harbor.nitro_environment:NitroEnvironment",
        "README must tell users the Harbor import path",
    )
    require(
        plan,
        "Production Harbor Nitro EIF",
        "PLAN must stay honest about the remaining production Harbor Nitro EIF gap",
    )

    print("MVP entrypoint lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
