#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "programs/solrl-registry/src/lib.rs"
REGISTRY_FLOW_TEST = ROOT / "programs/solrl-registry/tests/registry_flow.rs"
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
    source_compact = re.sub(r"\s+", "", source)
    settle = function_body(source, "settle_claim")
    hook = function_body(source, "transfer_hook")
    slash = function_body(source, "slash_operator")
    withdraw = function_body(source, "withdraw_stake")
    registry_flow_test = REGISTRY_FLOW_TEST.read_text(encoding="utf-8")

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
        fail("transfer hook must keep the narrow TransferGuard check for direct token transfers")
    if "arm_transfer_guard" not in settle:
        fail("settle_claim must retain scoped transfer metadata before payout")
    if "close_transfer_guard" not in settle:
        fail("settle_claim must close the temporary TransferGuard after payout")
    if "close_transfer_guard" not in slash:
        fail("slash_operator must close the temporary TransferGuard after slashing")
    if "close_transfer_guard" not in withdraw:
        fail("withdraw_stake must close the temporary TransferGuard after withdrawal")
    if "transfer_stake_to_withdraw_destination" not in withdraw:
        fail("withdraw_stake must transfer operator stake through Token-2022")
    if "ExtraAccountMetaList::init" not in source:
        fail("missing ExtraAccountMetaList initialization")
    if "ExtraAccountMeta::new_with_seeds" not in source:
        fail("ExtraAccountMetaList must dynamically resolve the TransferGuard PDA")
    for required_seed in (
        'Seed::Literal{bytes:b"transfer_guard".to_vec(),}',
        "Seed::AccountKey{index:0}",
        "Seed::AccountKey{index:1}",
        "Seed::AccountKey{index:2}",
        "Seed::AccountKey{index:3}",
        "Seed::InstructionData{index:8,length:8,}",
    ):
        if required_seed not in source_compact:
            fail(f"TransferGuard ExtraAccountMetaList is missing seed: {required_seed}")
    if "expires_slot" in source:
        fail("TransferGuard must not use expires_slot; hook guards are single-slot armed transfers")
    if "armed_slot" not in source:
        fail("TransferGuard must store the slot it was armed in")

    test_requirements = (
        "settle_claim_transfers_token2022_balance_with_registry_pda_authority",
        "spl_token_2022::processor::Processor::process",
        "token_instruction::initialize_mint2",
        "token_instruction::initialize_account3",
        "token_instruction::mint_to",
        "new_ed25519_instruction",
        "assert_eq!(escrow_after, 0)",
        "assert_eq!(payout_after, claim.amount)",
    )
    for requirement in test_requirements:
        if requirement not in registry_flow_test:
            fail(f"registry_flow.rs is missing Token-2022 balance proof: {requirement}")
    if "transfer_hook_instruction::initialize" in registry_flow_test:
        fail(
            "registry_flow Token-2022 balance proof must not rely on reentrant same-program transfer hooks"
        )

    combined_docs = README.read_text(encoding="utf-8") + "\n" + PLAN.read_text(encoding="utf-8")
    forbidden = (
        "hook verifies the full claim",
        "full claim verification in the transfer hook",
        "hook-side claim verification is implemented",
        "Token-2022 hook performs the entire claim check",
        "requires the hook to consume",
        "hook to consume that guard",
        "watching the hook fire",
        "hook fires through the real token program",
    )
    for phrase in forbidden:
        if phrase.lower() in combined_docs.lower():
            fail(f"docs overclaim Token-2022 hook behavior: {phrase}")
    required_doc_phrases = (
        "registry PDA authority",
        "local-validator Token-2022 balance test",
    )
    for phrase in required_doc_phrases:
        if phrase.lower() not in combined_docs.lower():
            fail(f"docs must name the current Token-2022 proof boundary: {phrase}")

    print("Token-2022 lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
