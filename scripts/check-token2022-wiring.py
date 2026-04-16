#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "programs/solrl-registry/src/lib.rs"
README = ROOT / "README.md"
PLAN = ROOT / "PLAN.md"


def fail(message: str) -> None:
    print(f"Token-2022 lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"(?:pub\s+)?fn {name}\([^)]*\).*?\{{", source, re.S)
    if not match:
        fail(f"missing function {name}")
    start = match.end()
    depth = 1
    index = start
    while index < len(source):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
        index += 1
    fail(f"could not parse function {name}")


def main() -> int:
    source = REGISTRY.read_text(encoding="utf-8")
    settle = function_body(source, "settle_claim")
    hook = function_body(source, "transfer_hook")
    slash = function_body(source, "slash_operator")

    if "transfer_escrow_to_operator" not in settle:
        fail("settle_claim must execute the escrow payout")
    if "spl_token_2022::instruction::transfer_checked" not in source:
        fail("registry must use Token-2022 transfer_checked")
    if "assert_transferring" not in hook:
        fail("transfer hook must require Token-2022 transferring flag")
    if "ctx.accounts.config.require_not_paused()" not in hook:
        fail("transfer hook must check pause state")
    if "ctx.accounts.config.token_mint" not in hook:
        fail("transfer hook must guard the configured mint")
    if "consume_transfer_guard" not in hook:
        fail("transfer hook must consume a TransferGuard")
    if "arm_transfer_guard" not in settle:
        fail("settle_claim must arm a TransferGuard before payout")
    if "TransferGuard" not in source:
        fail("registry must define a TransferGuard account")
    if "ctx.accounts.transfer_guard.exit(ctx.program_id)" not in settle:
        fail("settle_claim must flush the armed TransferGuard before Token-2022 CPI")
    if "TRANSFER_GUARD_STATUS_CONSUMED" not in settle:
        fail("settle_claim must fail unless the Token-2022 hook consumed the TransferGuard")
    if "TRANSFER_GUARD_STATUS_CONSUMED" not in slash:
        fail("slash_operator must fail unless the Token-2022 hook consumed the TransferGuard")
    if "ExtraAccountMetaList::init" not in source:
        fail("missing ExtraAccountMetaList initialization")

    combined_docs = README.read_text(encoding="utf-8") + "\n" + PLAN.read_text(encoding="utf-8")
    forbidden = (
        "hook verifies the full claim",
        "full claim verification in the transfer hook",
        "hook-side claim verification is implemented",
        "Token-2022 hook performs the entire claim check",
    )
    for phrase in forbidden:
        if phrase.lower() in combined_docs.lower():
            fail(f"docs overclaim Token-2022 hook behavior: {phrase}")

    print("Token-2022 lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
