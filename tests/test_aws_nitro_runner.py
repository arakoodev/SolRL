from __future__ import annotations

import os
from pathlib import Path

import pytest

from solrl_core.aws_nitro_runner import (
    AwsNitroRunnerError,
    GitSource,
    RunnerConfig,
    audit_counts,
    audit_has_resources,
    audit_resources,
    cleanup_run_resources,
    make_config,
    normalise_aws_env,
    parse_remote_markers,
    public_clone_url,
    resource_tags,
    resolve_git_source,
    remote_smoke_script,
    tags_match,
)


class FakeEc2:
    def __init__(self) -> None:
        self.instances = [
            {
                "InstanceId": "i-owned",
                "State": {"Name": "running"},
                "Tags": [{"Key": "Project", "Value": "SolRL"}, {"Key": "SolRLRunId", "Value": "run-a"}],
            },
            {
                "InstanceId": "i-terminated",
                "State": {"Name": "terminated"},
                "Tags": [{"Key": "Project", "Value": "SolRL"}, {"Key": "SolRLRunId", "Value": "run-a"}],
            },
            {
                "InstanceId": "i-other",
                "State": {"Name": "running"},
                "Tags": [{"Key": "Project", "Value": "Other"}, {"Key": "SolRLRunId", "Value": "run-b"}],
            },
        ]
        self.security_groups = [
            {
                "GroupId": "sg-owned",
                "Tags": [{"Key": "Project", "Value": "SolRL"}, {"Key": "SolRLRunId", "Value": "run-a"}],
            }
        ]
        self.volumes = [
            {
                "VolumeId": "vol-owned",
                "State": "available",
                "Tags": [{"Key": "Project", "Value": "SolRL"}, {"Key": "SolRLRunId", "Value": "run-a"}],
            }
        ]
        self.terminated_instances: list[str] = []
        self.deleted_security_groups: list[str] = []
        self.deleted_volumes: list[str] = []

    class _Waiter:
        def wait(self, **_kwargs: object) -> None:
            return None

    @staticmethod
    def _matches_tags(resource: dict, filters: list[dict]) -> bool:
        tags = {tag["Key"]: tag["Value"] for tag in resource.get("Tags", [])}
        state = resource.get("State", {})
        state_name = state.get("Name") if isinstance(state, dict) else state
        for item in filters:
            name = item["Name"]
            values = item["Values"]
            if name == "instance-state-name" and state_name not in values:
                return False
            if name.startswith("tag:") and tags.get(name.removeprefix("tag:")) not in values:
                return False
        return True

    def describe_instances(self, Filters: list[dict] | None = None, InstanceIds: list[str] | None = None) -> dict:
        if InstanceIds is not None:
            return {
                "Reservations": [
                    {"Instances": [instance for instance in self.instances if instance["InstanceId"] in InstanceIds]}
                ]
            }
        assert Filters is not None
        return {
            "Reservations": [
                {"Instances": [instance for instance in self.instances if self._matches_tags(instance, Filters)]}
            ]
        }

    def describe_security_groups(
        self,
        Filters: list[dict] | None = None,
        GroupIds: list[str] | None = None,
    ) -> dict:
        if GroupIds is not None:
            return {"SecurityGroups": [group for group in self.security_groups if group["GroupId"] in GroupIds]}
        assert Filters is not None
        return {
            "SecurityGroups": [group for group in self.security_groups if self._matches_tags(group, Filters)]
        }

    def describe_volumes(self, Filters: list[dict] | None = None, VolumeIds: list[str] | None = None) -> dict:
        if VolumeIds is not None:
            return {"Volumes": [volume for volume in self.volumes if volume["VolumeId"] in VolumeIds]}
        assert Filters is not None
        return {"Volumes": [volume for volume in self.volumes if self._matches_tags(volume, Filters)]}

    def terminate_instances(self, InstanceIds: list[str]) -> None:
        self.terminated_instances.extend(InstanceIds)
        for instance in self.instances:
            if instance["InstanceId"] in InstanceIds:
                instance["State"]["Name"] = "terminated"

    def delete_security_group(self, GroupId: str) -> None:
        self.deleted_security_groups.append(GroupId)
        self.security_groups = [group for group in self.security_groups if group["GroupId"] != GroupId]

    def delete_volume(self, VolumeId: str) -> None:
        self.deleted_volumes.append(VolumeId)
        self.volumes = [volume for volume in self.volumes if volume["VolumeId"] != VolumeId]

    def get_waiter(self, _name: str) -> _Waiter:
        return self._Waiter()


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


def test_public_clone_url_converts_github_ssh_remote() -> None:
    assert public_clone_url("git@github.com:arakoodev/SolRL.git") == "https://github.com/arakoodev/SolRL.git"
    assert public_clone_url("ssh://git@github.com/arakoodev/SolRL.git") == "https://github.com/arakoodev/SolRL.git"


def test_resolve_git_source_prefers_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLRL_NITRO_GIT_URL", "https://github.com/arakoodev/SolRL.git")
    monkeypatch.setenv("SOLRL_NITRO_GIT_REF", "main")

    source = resolve_git_source()

    assert source == GitSource("https://github.com/arakoodev/SolRL.git", "main")


def test_make_config_allows_root_volume_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLRL_NITRO_ROOT_VOLUME_GIB", "80")

    config = make_config("solrl-test", Path("artifacts/test"))

    assert config.root_volume_gib == 80


def test_tags_match_rejects_cross_run_resource() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))
    tags = resource_tags(config)
    wrong_run_tags = [dict(tag) for tag in tags]
    for tag in wrong_run_tags:
        if tag["Key"] == "SolRLRunId":
            tag["Value"] = "someone-elses-run"

    assert not tags_match(wrong_run_tags, config)


def test_project_audit_counts_only_active_solrl_resources() -> None:
    audit = audit_resources(FakeEc2())

    assert audit_counts(audit) == {"instances": 1, "security_groups": 1, "volumes": 1}
    assert audit_has_resources(audit)
    assert audit["instances"][0]["id"] == "i-owned"


def test_run_audit_scopes_exact_run_id() -> None:
    audit = audit_resources(FakeEc2(), "run-missing")

    assert audit_counts(audit) == {"instances": 0, "security_groups": 0, "volumes": 0}
    assert not audit_has_resources(audit)


def test_cleanup_run_resources_deletes_only_exact_tagged_resources() -> None:
    ec2 = FakeEc2()
    config = make_config("run-a", Path("artifacts/test"))

    result = cleanup_run_resources(ec2, config, log=lambda _message: None)

    assert ec2.terminated_instances == ["i-owned"]
    assert ec2.deleted_security_groups == ["sg-owned"]
    assert ec2.deleted_volumes == ["vol-owned"]
    assert audit_counts(result["remaining"]) == {"instances": 0, "security_groups": 0, "volumes": 0}
    assert ec2.instances[2]["InstanceId"] == "i-other"


def final_block(**overrides: str) -> str:
    markers = {
        "SOLRL_STATUS": "OK",
        "SOLRL_RUN_ID": "solrl-test",
        "SOLRL_GIT_REF": "abcdef123456",
        "SOLRL_EIF_SHA384": "a" * 96,
        "SOLRL_NITRO_ROOT_SHA256": "b" * 64,
        "SOLRL_PCR0": "c" * 96,
        "SOLRL_PCR1": "d" * 96,
        "SOLRL_PCR2": "e" * 96,
        "SOLRL_PCR16": "f" * 96,
    }
    markers.update(overrides)
    return "\n".join(
        [
            "boot noise that should be ignored",
            "SOLRL_RESULT_BEGIN",
            *[f"{key}={value}" for key, value in markers.items()],
            "SOLRL_RESULT_END",
        ]
    )


def test_parse_remote_markers_reads_only_final_result_block() -> None:
    stdout = "\n".join(
        [
            final_block(SOLRL_RUN_ID="old-run", SOLRL_PCR0="1" * 96),
            "more boot noise",
            final_block(SOLRL_RUN_ID="new-run", SOLRL_PCR0="2" * 96),
        ]
    )

    markers = parse_remote_markers(stdout)

    assert markers["SOLRL_RUN_ID"] == "new-run"
    assert markers["SOLRL_PCR0"] == "2" * 96


def test_parse_remote_markers_rejects_missing_final_block() -> None:
    with pytest.raises(AwsNitroRunnerError, match="SOLRL_RESULT"):
        parse_remote_markers("SOLRL_STATUS=OK\nSOLRL_PCR0=cc")


def test_parse_remote_markers_rejects_missing_marker() -> None:
    with pytest.raises(AwsNitroRunnerError, match="SOLRL_PCR16"):
        parse_remote_markers(final_block(SOLRL_PCR16=""))


def test_parse_remote_markers_rejects_failed_status() -> None:
    stdout = "\n".join(
        [
            "SOLRL_RESULT_BEGIN",
            "SOLRL_STATUS=FAILED",
            "SOLRL_PHASE=build_eif",
            "SOLRL_ERROR_TAIL_BEGIN",
            "nix exploded",
            "SOLRL_ERROR_TAIL_END",
            "SOLRL_RESULT_END",
        ]
    )

    with pytest.raises(AwsNitroRunnerError, match="build_eif"):
        parse_remote_markers(stdout)


def test_remote_script_configures_allocator_before_start() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))
    source = GitSource("https://github.com/arakoodev/SolRL.git", "abcdef123456")

    script = remote_smoke_script(config, "aa", "bb", "cc", source)

    assert "systemctl enable --now nitro-enclaves-allocator.service" not in script
    assert "exec >\"$LOG_DIR/user-data.log\" 2>&1" in script
    assert "tee /var/log" not in script
    assert "SOLRL_RESULT_BEGIN" in script
    assert "SOLRL_RESULT_END" in script
    assert "SOLRL_PHASE_START=$PHASE" in script
    assert "SOLRL_PHASE_TIMEOUT_SECONDS=$timeout_seconds" in script
    assert "tail -80" in script
    assert "shutdown -h now" in script
    assert "OVERALL_TIMEOUT_SECONDS=5400" in script
    assert "start_watchdog" in script
    assert "SOLRL_PHASE=overall_timeout" in script
    assert "/run/solrl.done" in script
    assert "/run/solrl.phase" in script
    assert "kill -TERM \"$phase_pid\"" in script
    assert "kill -KILL \"$phase_pid\"" in script
    assert "yum update -y" not in script
    assert "run_phase packages 900 phase_packages" in script
    assert "run_phase build_eif 5400 phase_build_eif" in script
    assert "run_phase attestation 300 phase_attestation" in script
    assert "systemctl daemon-reload" in script
    assert "docker build" not in script
    assert "nitro-cli build-enclave" not in script
    assert "nixos.org/nix/install" in script
    assert "nix build .#solrl-nitro-worker-eif" in script
    assert "'cryptography<42'" in script
    assert "git clone \"$git_url\" \"$SRC_DIR\"" in script
    assert "git checkout --detach \"abcdef123456\"" in script
    assert "--build-arg SOLRL_USER_DATA_HEX" not in script
    assert "USER_DATA_HEX=aa" in script
    assert "PUBLIC_KEY_HEX=bb" in script
    assert "NONCE_HEX=cc" in script
    assert "sock.sendall(payload.encode(\"ascii\"))" in script
    assert "verify-attestation" in script
    assert script.index("verify-attestation") < script.index("SOLRL_STATUS=OK")
    assert "nitro-cli describe-eif" in script
    assert "SOLRL_EIF_SHA384=" in script
    assert "SOLRL_NITRO_ROOT_SHA256=" in script
    assert "SOLRL_PCR16=" in script
    assert "SOLRL_ATTESTATION_HEX" not in script
    assert "SOLRL_BUILD_JSON_B64" not in script
    assert script.index("cat >/etc/nitro_enclaves/allocator.yaml") < script.index("nitro-cli run-enclave")
    assert script.index("systemctl restart nitro-enclaves-allocator.service") < script.index("nitro-cli run-enclave")
    assert len(script.encode("utf-8")) <= 16_384


def test_remote_script_rejects_shell_unsafe_run_id() -> None:
    config = make_config("solrl-test;rm", Path("artifacts/test"))
    source = GitSource("https://github.com/arakoodev/SolRL.git", "abcdef123456")

    with pytest.raises(AwsNitroRunnerError, match="run_id"):
        remote_smoke_script(config, "aa", "bb", "cc", source)


def test_remote_script_rejects_non_hex_template_values() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))
    source = GitSource("https://github.com/arakoodev/SolRL.git", "abcdef123456")

    with pytest.raises(AwsNitroRunnerError, match="user_data_hex"):
        remote_smoke_script(config, "aa;rm", "bb", "cc", source)
    with pytest.raises(AwsNitroRunnerError, match="public_key_hex"):
        remote_smoke_script(config, "aa", "not-hex", "cc", source)
    with pytest.raises(AwsNitroRunnerError, match="nonce_hex"):
        remote_smoke_script(config, "aa", "bb", "c", source)


def test_remote_script_rejects_private_git_source() -> None:
    config = make_config("solrl-test", Path("artifacts/test"))
    source = GitSource("https://token@github.com/arakoodev/SolRL.git", "abcdef123456")

    with pytest.raises(AwsNitroRunnerError, match="public HTTPS"):
        remote_smoke_script(config, "aa", "bb", "cc", source)


def test_remote_script_rejects_invalid_vsock_port() -> None:
    config = RunnerConfig(
        region="us-east-1",
        run_id="solrl-test",
        name="SolRL-test",
        instance_type="m5.xlarge",
        artifact_dir=Path("artifacts/test"),
        vsock_port=70_000,
    )

    with pytest.raises(AwsNitroRunnerError, match="vsock_port"):
        remote_smoke_script(config, "aa", "bb", "cc", GitSource("https://github.com/arakoodev/SolRL.git", "abc"))
