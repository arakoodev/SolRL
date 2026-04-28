#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "why.md",
    ROOT / ".agents/skills/solrl-framework/SKILL.md",
    ROOT / ".agents/skills/solrl-framework/agents/openai.yaml",
    ROOT / ".agents/skills/solrl-framework/references/architecture.md",
    ROOT / ".agents/skills/solrl-framework/references/claim-registry.md",
    ROOT / ".agents/skills/solrl-framework/references/commands.md",
    ROOT / ".agents/skills/solrl-framework/references/troubleshooting.md",
)

FORBIDDEN_PATTERNS = (
    re.compile(r"\bcompetition\b", re.IGNORECASE),
    re.compile(r"\bhackathon\b", re.IGNORECASE),
    re.compile(r"\bcontest\b", re.IGNORECASE),
    re.compile(r"\bjudges?\b", re.IGNORECASE),
    re.compile(r"\bdemo\b", re.IGNORECASE),
    re.compile(r"magical claims", re.IGNORECASE),
    re.compile(r"how a judge", re.IGNORECASE),
    re.compile(r"what to say", re.IGNORECASE),
)


def fail(message: str) -> None:
    print(f"public docs language lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    violations: list[str] = []
    for path in DOC_PATHS:
        if not path.exists():
            fail(f"missing public doc path: {path.relative_to(ROOT)}")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")

    if violations:
        fail(
            "public README and AI skill docs must use neutral open source verification language, not "
            "competition/judge framing:\n" + "\n".join(violations)
        )

    print("public docs language lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
