#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github/dependabot.yml"


def fail(message: str) -> None:
    print(f"dependabot config lint failed: {message}", file=sys.stderr)
    sys.exit(1)


def require(text: str, needle: str, message: str) -> None:
    if needle not in text:
        fail(message)


def block_for(text: str, ecosystem: str, directory: str) -> str:
    pattern = re.compile(
        rf'(?ms)^  - package-ecosystem: "{re.escape(ecosystem)}"\n'
        rf".*?^    directory: \"{re.escape(directory)}\""
        rf".*?(?=^  - package-ecosystem: |\Z)"
    )
    match = pattern.search(text)
    if not match:
        fail(f"missing {ecosystem} Dependabot block for {directory}")
    return match.group(0)


def main() -> int:
    if not DEPENDABOT.exists():
        fail("missing .github/dependabot.yml")

    text = DEPENDABOT.read_text(encoding="utf-8")
    require(text, "version: 2", "dependabot.yml must use version 2")

    root_cargo = block_for(text, "cargo", "/")
    worker_cargo = block_for(text, "cargo", "/crates/solrl-nitro-worker")
    block_for(text, "nix", "/")
    block_for(text, "github-actions", "/")
    block_for(text, "docker", "/docker")
    block_for(text, "docker-compose", "/")
    block_for(text, "terraform", "/infra/localstack")

    require(
        root_cargo,
        'dependency-type: "direct"',
        "root Cargo Dependabot must track direct dependencies only; transitive Solana lockfile updates create noisy failures",
    )
    if 'dependency-type: "all"' in root_cargo or 'dependency-type: "indirect"' in root_cargo:
        fail("root Cargo Dependabot must not allow all/indirect dependencies")

    for excluded in ("vendor/**", "target/**", "artifacts/**", "crates/solrl-nitro-worker/**"):
        require(root_cargo, excluded, f"root Cargo Dependabot must exclude {excluded}")

    require(worker_cargo, "nitro-worker-rust", "Nitro worker must have its own Cargo Dependabot group")
    require(text, "multi-ecosystem-groups:", "infrastructure ecosystems must share one grouped Dependabot lane")

    dockerfile_with_latest = []
    for dockerfile in (ROOT / "docker").glob("Dockerfile*"):
        for lineno, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"FROM\s+\S+:latest(?:\s|$)", line):
                dockerfile_with_latest.append(f"{dockerfile.relative_to(ROOT)}:{lineno}")
    if dockerfile_with_latest:
        fail("Docker base images must not use :latest: " + ", ".join(dockerfile_with_latest))

    print("dependabot config lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
