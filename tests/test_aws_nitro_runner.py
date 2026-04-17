from __future__ import annotations

import os
from pathlib import Path

import pytest

from solrl_core.aws_nitro_runner import (
    AwsNitroRunnerError,
    make_config,
    normalise_aws_env,
    parse_remote_markers,
    resource_tags,
    remote_smoke_script,
    tags_match,
)


def test_normalise_aws_env_accepts_existing_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY", "access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    normalise_aws_env()

    assert os.environ["AWS_ACCESS_KEY_ID"] == "access"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert os.environ["AWS_DEFAULT_REGION"] == "us-east-1"


def test_resource_tags_carry_ownership_and_run_id() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))

    tags = resource_tags(config)

    assert tags_match(tags, config)
    assert {tag["Key"] for tag in tags} >= {"Project", "ManagedBy", "Purpose", "SolRLRunId", "ExpiresAt"}


def test_tags_match_rejects_cross_run_resource() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))
    tags = resource_tags(config)
    wrong_run_tags = [dict(tag) for tag in tags]
    for tag in wrong_run_tags:
        if tag["Key"] == "SolRLRunId":
            tag["Value"] = "someone-elses-run"

    assert not tags_match(wrong_run_tags, config)


def test_parse_remote_markers_requires_attestation_and_expected_payload() -> None:
    stdout = "\n".join(
        [
            "noise",
            "SOLRL_STATUS=OK",
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX=cc",
        ]
    )

    markers = parse_remote_markers(stdout)

    assert markers["SOLRL_ATTESTATION_HEX"] == "cc"
    assert markers["SOLRL_EXPECTED_USER_DATA_HEX"] == "aa"


def test_parse_remote_markers_reassembles_chunked_attestation() -> None:
    stdout = "\n".join(
        [
            "SOLRL_STATUS=OK",
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX_BEGIN",
            "SOLRL_ATTESTATION_HEX_WIDTH=2",
            "SOLRL_ATTESTATION_HEX_CHUNKS=3",
            "SOLRL_ATTESTATION_HEX_CHUNK=0000:cc",
            "SOLRL_ATTESTATION_HEX_CHUNK=ci-info: no authorized ssh keys fingerprints found",
            "[  122.0] cloud-init: SOLRL_ATTESTATION_HEX_CHUNK=0000:ignored",
            "SOLRL_ATTESTATION_HEX_CHUNK=0001:dd[2026-04-17]",
            "[  122.1] cloud-init: SOLRL_ATTESTATION_HEX_CHUNK=0001:dd",
            "SOLRL_ATTESTATION_HEX_CHUNK=0002:ee",
            "SOLRL_ATTESTATION_HEX_END",
        ]
    )

    markers = parse_remote_markers(stdout)

    assert markers["SOLRL_ATTESTATION_HEX"] == "ccddee"


def test_parse_remote_markers_rejects_corrupted_attestation_hex() -> None:
    stdout = "\n".join(
        [
            "SOLRL_STATUS=OK",
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX_WIDTH=2",
            "SOLRL_ATTESTATION_HEX_CHUNKS=1",
            "SOLRL_ATTESTATION_HEX_CHUNK=0000:cc[2026-04-17]dd",
        ]
    )

    with pytest.raises(AwsNitroRunnerError, match="chunks corrupt"):
        parse_remote_markers(stdout)


def test_parse_remote_markers_rejects_missing_indexed_attestation_chunk() -> None:
    stdout = "\n".join(
        [
            "SOLRL_STATUS=OK",
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX_WIDTH=2",
            "SOLRL_ATTESTATION_HEX_CHUNKS=2",
            "SOLRL_ATTESTATION_HEX_CHUNK=0000:cc",
        ]
    )

    with pytest.raises(AwsNitroRunnerError, match="chunks incomplete"):
        parse_remote_markers(stdout)


def test_parse_remote_markers_rejects_missing_marker() -> None:
    with pytest.raises(AwsNitroRunnerError, match="SOLRL_ATTESTATION_HEX"):
        parse_remote_markers("SOLRL_STATUS=OK\nSOLRL_EXPECTED_USER_DATA_HEX=aa")


def test_parse_remote_markers_rejects_failed_status() -> None:
    stdout = "\n".join(
        [
            "SOLRL_STATUS=FAILED",
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX=cc",
        ]
    )

    with pytest.raises(AwsNitroRunnerError, match="FAILED"):
        parse_remote_markers(stdout)


def test_remote_script_configures_allocator_before_start() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))

    script = remote_smoke_script(config, "aa", "bb", "cc")

    assert "systemctl enable --now nitro-enclaves-allocator.service" not in script
    assert "systemctl status nitro-enclaves-allocator.service --no-pager -l" in script
    assert "journalctl -u nitro-enclaves-allocator.service --no-pager -n 120" in script
    assert "systemctl daemon-reload" in script
    assert "export NITRO_CLI_ARTIFACTS=/var/lib/solrl/nitro-artifacts" in script
    assert "export NITRO_CLI_BLOBS=/usr/share/nitro_enclaves/blobs" in script
    assert "Request::ExtendPCR { index: 16" in script
    assert "Request::LockPCR { index: 16" in script
    assert "Request::DescribePCR { index: 16" in script
    assert "SOLRL_ATTESTATION_HEX_CHUNK=" in script
    assert "SOLRL_ATTESTATION_HEX_WIDTH=" in script
    assert "SOLRL_ATTESTATION_HEX_CHUNKS=" in script
    assert "SOLRL_ATTESTATION_HEX_PASS=" in script
    assert 'fold -w "$ATTESTATION_CHUNK_WIDTH"' in script
    assert "%04d:%s" in script
    assert "echo SOLRL_ATTESTATION_HEX=\"$(cat /tmp/solrl-attestation.hex)\"" not in script
    assert script.index("cat >/etc/nitro_enclaves/allocator.yaml") < script.index(
        "systemctl restart nitro-enclaves-allocator.service"
    )
    assert script.index("export NITRO_CLI_ARTIFACTS") < script.index("nitro-cli build-enclave")
