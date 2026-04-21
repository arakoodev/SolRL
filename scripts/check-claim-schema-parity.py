#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "crates/solrl-claim/src/lib.rs"
PYTHON = ROOT / "python/solrl_core/claim.py"


def fail(message: str) -> None:
    print(f"schema parity lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def rust_struct_fields(source: str, name: str) -> list[str]:
    match = re.search(rf"pub struct {name} \{{(?P<body>.*?)\n\}}", source, re.S)
    if not match:
        fail(f"missing Rust struct {name}")
    return re.findall(r"pub\s+([a-zA-Z0-9_]+)\s*:", match.group("body"))


def rust_domain(source: str, name: str) -> str:
    match = re.search(rf'pub const {name}: &\[u8\] = b"([^"]+)";', source)
    if not match:
        fail(f"missing Rust domain {name}")
    return match.group(1)


def rust_function_body(source: str, name: str) -> str:
    match = re.search(rf"pub fn {name}\([^)]*\).*?\{{", source, re.S)
    if not match:
        fail(f"missing Rust function {name}")
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
    fail(f"could not parse Rust function {name}")


def python_tuple(source: str, name: str) -> list[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
                    return list(value)
    fail(f"missing Python tuple {name}")


def python_domain(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, bytes):
                        fail(f"Python {name} must be bytes")
                    return value.decode()
    fail(f"missing Python domain {name}")


def python_function_body(source: str, name: str) -> str:
    match = re.search(rf"^def {name}\(.*?\)(?:\s*->\s*[^:]+)?:\n(?P<body>(?:    .*\n)+)", source, re.M)
    if not match:
        fail(f"missing Python function {name}")
    return match.group("body")


def assert_same(label: str, left: list[str] | str, right: list[str] | str) -> None:
    if left != right:
        fail(f"{label} mismatch\nRust:   {left}\nPython: {right}")


def main() -> int:
    rust = RUST.read_text(encoding="utf-8")
    python = PYTHON.read_text(encoding="utf-8")

    assert_same("ClaimV1 fields", rust_struct_fields(rust, "ClaimV1"), python_tuple(python, "CLAIM_FIELDS"))
    assert_same(
        "SlashClaimV1 fields",
        rust_struct_fields(rust, "SlashClaimV1"),
        python_tuple(python, "SLASH_CLAIM_FIELDS"),
    )
    assert_same(
        "Pcr16Components fields",
        rust_struct_fields(rust, "Pcr16Components"),
        python_tuple(python, "PCR16_FIELDS"),
    )

    for name in ("CLAIM_DOMAIN", "SLASH_CLAIM_DOMAIN", "PCR16_DOMAIN"):
        assert_same(name, rust_domain(rust, name), python_domain(python, name))

    for name in ("fixed_bytes", "pubkey_bytes"):
        body = python_function_body(python, name)
        if "hashlib" in body or ".digest(" in body or "sha256" in body:
            fail(f"Python {name} must reject invalid input, not hash/coerce it")

    rust_pcr16 = rust_function_body(rust, "pcr16_digest")
    python_pcr16 = python_function_body(python, "pcr16_digest")
    if "pcr16_user_data" not in rust or "pcr16_user_data" not in python:
        fail("Rust and Python must expose pcr16_user_data for Nitro ExtendPCR input")
    if "pcr16_user_data" not in rust_pcr16 or "[0u8; 48]" not in rust_pcr16:
        fail("Rust pcr16_digest must model Nitro PCR16 extension from a zero PCR")
    if "pcr16_user_data" not in python_pcr16 or "bytes(48)" not in python_pcr16:
        fail("Python pcr16_digest must model Nitro PCR16 extension from a zero PCR")

    print("schema parity lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
