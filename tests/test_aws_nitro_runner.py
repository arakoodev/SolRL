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
            "SOLRL_EXPECTED_USER_DATA_HEX=aa",
            "SOLRL_EXPECTED_PUBLIC_KEY_HEX=bb",
            "SOLRL_BUILD_JSON_B64=e30=",
            "SOLRL_ATTESTATION_HEX=cc",
        ]
    )

    markers = parse_remote_markers(stdout)

    assert markers["SOLRL_ATTESTATION_HEX"] == "cc"
    assert markers["SOLRL_EXPECTED_USER_DATA_HEX"] == "aa"


def test_parse_remote_markers_rejects_missing_marker() -> None:
    with pytest.raises(AwsNitroRunnerError, match="SOLRL_ATTESTATION_HEX"):
        parse_remote_markers("SOLRL_EXPECTED_USER_DATA_HEX=aa")
