from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import botocore.exceptions
import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils


AWS_ROOT_KEY_HEX = (
    "fc0254eba608c1f36870e29ada90be46383292736e894bfff672d989444b5051e534a4b1f6dbe3c0"
    "bc581a32b7b176070ede12d69a3fea211b66e752cf7dd1dd095f6f1370f4170843d9dc100121e4cf"
    "63012809664487c9796284304dc53ff4"
)
AWS_ROOT_KEY = bytes.fromhex(AWS_ROOT_KEY_HEX)
DEFAULT_AMI_NAME_FILTER = "amzn2-ami-kernel-5.10-hvm-*-x86_64-gp2"
DEFAULT_INSTANCE_TYPE = "m5.xlarge"
DEFAULT_REGION = "us-east-1"
DEFAULT_VSOCK_PORT = 5005


class AwsNitroRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerConfig:
    region: str
    run_id: str
    name: str
    instance_type: str
    artifact_dir: Path
    vsock_port: int = DEFAULT_VSOCK_PORT
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class DecodedAttestation:
    timestamp_ms: int
    pcrs: dict[int, bytes]
    public_key: bytes
    user_data: bytes
    root_public_key: bytes

    def to_summary(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "pcrs": {str(k): v.hex() for k, v in sorted(self.pcrs.items())},
            "public_key": self.public_key.hex(),
            "user_data": self.user_data.hex(),
            "root_public_key": self.root_public_key.hex(),
        }


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalise_aws_env() -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY", "")
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get(
        "AWS_SECRET_KEY", ""
    )
    if os.environ.get("AWS_SESSION_TOKEN") is None:
        os.environ["AWS_SESSION_TOKEN"] = ""
    os.environ["AWS_DEFAULT_REGION"] = (
        os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or DEFAULT_REGION
    )
    if not os.environ["AWS_ACCESS_KEY_ID"] or not os.environ["AWS_SECRET_ACCESS_KEY"]:
        raise AwsNitroRunnerError(
            "Missing AWS credentials. Expected AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or AWS_ACCESS_KEY/AWS_SECRET_ACCESS_KEY."
        )


def require_docker() -> None:
    if os.environ.get("SOLRL_IN_DOCKER") != "1":
        raise AwsNitroRunnerError(
            "AWS Nitro runner must run inside Docker. Use: "
            "docker compose run --rm aws-nitro-runner"
        )


def make_config(run_id: str | None = None, artifact_root: Path = Path("artifacts/aws-nitro")) -> RunnerConfig:
    generated_run_id = run_id or f"solrl-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
    name = f"SolRL-{generated_run_id}"
    return RunnerConfig(
        region=os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION,
        run_id=generated_run_id,
        name=name,
        instance_type=os.environ.get("SOLRL_NITRO_INSTANCE_TYPE", DEFAULT_INSTANCE_TYPE),
        artifact_dir=artifact_root / generated_run_id,
    )


def resource_tags(config: RunnerConfig, purpose: str = "nitro-smoke") -> list[dict[str, str]]:
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {"Key": "Name", "Value": config.name},
        {"Key": "Project", "Value": "SolRL"},
        {"Key": "ManagedBy", "Value": "SolRL"},
        {"Key": "Purpose", "Value": purpose},
        {"Key": "SolRLRunId", "Value": config.run_id},
        {"Key": "ExpiresAt", "Value": expires_at},
    ]


def tag_specifications(config: RunnerConfig, resource_types: list[str]) -> list[dict[str, Any]]:
    tags = resource_tags(config)
    return [{"ResourceType": resource_type, "Tags": tags} for resource_type in resource_types]


def tags_match(tags: list[dict[str, str]], config: RunnerConfig) -> bool:
    tag_map = {tag["Key"]: tag["Value"] for tag in tags}
    return tag_map.get("Project") == "SolRL" and tag_map.get("SolRLRunId") == config.run_id


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_remote_markers(stdout: str) -> dict[str, str]:
    markers: dict[str, str] = {}
    attestation_candidates: dict[int, list[str]] = {}
    for line in stdout.splitlines():
        if "SOLRL_ATTESTATION_HEX_CHUNK=" in line:
            chunk = line.split("SOLRL_ATTESTATION_HEX_CHUNK=", 1)[1].strip()
            if ":" not in chunk:
                continue
            index, value = chunk.split(":", 1)
            if not index.isdigit():
                continue
            attestation_candidates.setdefault(int(index), []).append(value)
        elif "SOLRL_" in line and "=" in line:
            marker = line.split("SOLRL_", 1)[1]
            key_suffix, value = marker.split("=", 1)
            key = f"SOLRL_{key_suffix}"
            markers[key] = value.strip()
    if attestation_candidates:
        try:
            expected_chunks = int(markers.get("SOLRL_ATTESTATION_HEX_CHUNKS", "-1"))
            chunk_width = int(markers.get("SOLRL_ATTESTATION_HEX_WIDTH", "-1"))
        except ValueError as exc:
            raise AwsNitroRunnerError("remote Nitro attestation chunk metadata is invalid") from exc
        expected_indexes = set(range(expected_chunks))
        actual_indexes = set(attestation_candidates)
        if expected_chunks < 1 or actual_indexes != expected_indexes:
            missing = sorted(expected_indexes - actual_indexes)
            extra = sorted(actual_indexes - expected_indexes)
            raise AwsNitroRunnerError(
                f"remote Nitro attestation chunks incomplete; missing={missing}, extra={extra}"
            )
        if chunk_width < 2:
            raise AwsNitroRunnerError("remote Nitro attestation chunk width is invalid")
        accepted_chunks: list[str] = []
        corrupt_indexes: list[int] = []
        for index in range(expected_chunks):
            valid_values = []
            for value in attestation_candidates[index]:
                is_hex = len(value) % 2 == 0 and all(char in "0123456789abcdefABCDEF" for char in value)
                is_full_chunk = index < expected_chunks - 1 and len(value) == chunk_width
                is_last_chunk = index == expected_chunks - 1 and 0 < len(value) <= chunk_width
                if is_hex and (is_full_chunk or is_last_chunk):
                    valid_values.append(value)
            if not valid_values:
                corrupt_indexes.append(index)
                continue
            accepted_chunks.append(valid_values[0])
        if corrupt_indexes:
            raise AwsNitroRunnerError(f"remote Nitro attestation chunks corrupt; indexes={corrupt_indexes}")
        markers["SOLRL_ATTESTATION_HEX"] = "".join(accepted_chunks)
    required = {
        "SOLRL_STATUS",
        "SOLRL_ATTESTATION_HEX",
        "SOLRL_EXPECTED_USER_DATA_HEX",
        "SOLRL_EXPECTED_PUBLIC_KEY_HEX",
        "SOLRL_BUILD_JSON_B64",
    }
    missing = sorted(required - markers.keys())
    if missing:
        raise AwsNitroRunnerError(f"remote Nitro command did not return markers: {', '.join(missing)}")
    if markers["SOLRL_STATUS"] != "OK":
        raise AwsNitroRunnerError(f"remote Nitro command returned status {markers['SOLRL_STATUS']}")
    attestation_hex = markers["SOLRL_ATTESTATION_HEX"]
    if len(attestation_hex) % 2 != 0 or any(char not in "0123456789abcdefABCDEF" for char in attestation_hex):
        raise AwsNitroRunnerError("remote Nitro attestation marker is not valid hex")
    return markers


def _load_cose_sign1(document: bytes) -> tuple[bytes, bytes, bytes, dict[str, Any]]:
    decoded = cbor2.loads(document)
    if isinstance(decoded, cbor2.CBORTag):
        if decoded.tag != 18:
            raise AwsNitroRunnerError(f"unexpected COSE tag: {decoded.tag}")
        decoded = decoded.value
    if not isinstance(decoded, list) or len(decoded) != 4:
        raise AwsNitroRunnerError("attestation is not a COSE_Sign1 array")
    protected, _unprotected, payload, signature = decoded
    if not isinstance(protected, bytes) or not isinstance(payload, bytes) or not isinstance(signature, bytes):
        raise AwsNitroRunnerError("attestation COSE fields have invalid types")
    protected_map = cbor2.loads(protected) if protected else {}
    if protected_map.get(1) != -35:
        raise AwsNitroRunnerError(f"unexpected COSE algorithm: {protected_map.get(1)!r}")
    payload_map = cbor2.loads(payload)
    if not isinstance(payload_map, dict):
        raise AwsNitroRunnerError("attestation payload is not a CBOR map")
    return protected, payload, signature, payload_map


def _verify_cose_signature(protected: bytes, payload: bytes, signature: bytes, leaf: x509.Certificate) -> None:
    if len(signature) != 96:
        raise AwsNitroRunnerError(f"expected ES384 raw signature to be 96 bytes, got {len(signature)}")
    sig_structure = cbor2.dumps(["Signature1", protected, b"", payload])
    der_signature = utils.encode_dss_signature(
        int.from_bytes(signature[:48], "big"),
        int.from_bytes(signature[48:], "big"),
    )
    public_key = leaf.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise AwsNitroRunnerError("Nitro attestation leaf certificate is not ECDSA")
    public_key.verify(der_signature, sig_structure, ec.ECDSA(hashes.SHA384()))


def _verify_cert_signature(child: x509.Certificate, parent: x509.Certificate) -> None:
    public_key = parent.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        public_key.verify(
            child.signature,
            child.tbs_certificate_bytes,
            padding.PKCS1v15(),
            child.signature_hash_algorithm,
        )
        return
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(
            child.signature,
            child.tbs_certificate_bytes,
            ec.ECDSA(child.signature_hash_algorithm),
        )
        return
    raise AwsNitroRunnerError("unsupported certificate public key type")


def _verify_cert_chain(payload_map: dict[str, Any], timestamp_ms: int) -> bytes:
    leaf = x509.load_der_x509_certificate(payload_map["certificate"])
    bundle = [x509.load_der_x509_certificate(cert) for cert in reversed(payload_map["cabundle"])]
    chain = [leaf, *bundle]
    checked_at = dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc)
    for child, parent in zip(chain, chain[1:], strict=False):
        if child.issuer != parent.subject:
            raise AwsNitroRunnerError("Nitro certificate chain issuer mismatch")
        if checked_at < child.not_valid_before_utc or checked_at > child.not_valid_after_utc:
            raise AwsNitroRunnerError("Nitro certificate is not valid at attestation timestamp")
        _verify_cert_signature(child, parent)

    root = chain[-1]
    public_key = root.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise AwsNitroRunnerError("Nitro root certificate is not ECDSA")
    numbers = public_key.public_numbers()
    root_key = numbers.x.to_bytes(48, "big") + numbers.y.to_bytes(48, "big")
    if root_key != AWS_ROOT_KEY:
        raise AwsNitroRunnerError("Nitro root public key mismatch")
    return root_key


def verify_attestation_document(
    document: bytes,
    expected_user_data: bytes,
    expected_public_key: bytes,
) -> DecodedAttestation:
    protected, payload, signature, payload_map = _load_cose_sign1(document)
    timestamp_ms = int(payload_map["timestamp"])
    leaf = x509.load_der_x509_certificate(payload_map["certificate"])
    _verify_cose_signature(protected, payload, signature, leaf)
    root_public_key = _verify_cert_chain(payload_map, timestamp_ms)

    pcrs = payload_map["pcrs"]
    for index in (0, 1, 2, 16):
        if index not in pcrs:
            raise AwsNitroRunnerError(f"PCR{index} missing from attestation")
        if len(pcrs[index]) != 48:
            raise AwsNitroRunnerError(f"PCR{index} must be 48 bytes")
        if pcrs[index] == bytes(48):
            raise AwsNitroRunnerError(f"PCR{index} is all zero; non-debug Nitro smoke required")
    expected_pcr16 = hashlib.sha384(bytes(48) + expected_user_data).digest()
    if pcrs[16] != expected_pcr16:
        raise AwsNitroRunnerError("PCR16 does not match the expected ClaimV1 context extension")

    user_data = payload_map.get("user_data") or b""
    public_key = payload_map.get("public_key") or b""
    if user_data != expected_user_data:
        raise AwsNitroRunnerError("attestation user_data does not match ClaimV1 context hash")
    if public_key != expected_public_key:
        raise AwsNitroRunnerError("attestation public_key does not match expected worker key")

    return DecodedAttestation(
        timestamp_ms=timestamp_ms,
        pcrs={int(k): v for k, v in pcrs.items()},
        public_key=public_key,
        user_data=user_data,
        root_public_key=root_public_key,
    )


class AwsNitroRunner:
    def __init__(self, config: RunnerConfig, session: boto3.Session) -> None:
        self.config = config
        self.ec2 = session.client("ec2")
        self.sts = session.client("sts")
        self.instance_id = ""
        self.security_group_id = ""

    def log(self, message: str) -> None:
        line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        with (self.config.artifact_dir / "run.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def smoke(self) -> None:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._smoke()
        except botocore.exceptions.ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "Unknown")
            message = error.get("Message", str(exc))
            raise AwsNitroRunnerError(f"AWS API failed with {code}: {message}") from exc
        finally:
            self.cleanup()

    def _smoke(self) -> None:
        caller = self.sts.get_caller_identity()
        self._write_json("caller.json", {"Account": caller["Account"], "Arn": caller["Arn"]})
        self.log(f"authenticated AWS account: {caller['Account']}")

        vpc_id, subnet_id = self._default_network()
        ami_id = self._default_ami()
        self._create_security_group(vpc_id)
        self._launch_instance(ami_id, subnet_id)
        self._wait_for_instance()
        stdout = self._wait_for_console_smoke()
        markers = parse_remote_markers(stdout)
        self._persist_remote_outputs(markers)

        attestation = bytes.fromhex(markers["SOLRL_ATTESTATION_HEX"])
        decoded = verify_attestation_document(
            attestation,
            bytes.fromhex(markers["SOLRL_EXPECTED_USER_DATA_HEX"]),
            bytes.fromhex(markers["SOLRL_EXPECTED_PUBLIC_KEY_HEX"]),
        )
        self._write_json("attestation-summary.json", decoded.to_summary())
        self.log("Nitro attestation verified: AWS root, COSE signature, PCRs, user_data, public_key")

    def _default_network(self) -> tuple[str, str]:
        response = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        vpcs = response.get("Vpcs", [])
        if not vpcs:
            raise AwsNitroRunnerError(f"No default VPC found in {self.config.region}; refusing to create networking")
        vpc_id = vpcs[0]["VpcId"]
        subnets = self.ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        ).get("Subnets", [])
        if not subnets:
            raise AwsNitroRunnerError(f"No default subnet found in {vpc_id}; refusing to create networking")
        subnet_id = sorted(subnets, key=lambda item: item["SubnetId"])[0]["SubnetId"]
        self.log(f"default VPC/subnet: {vpc_id}/{subnet_id}")
        return vpc_id, subnet_id

    def _default_ami(self) -> str:
        if ami_id := os.environ.get("SOLRL_NITRO_AMI_ID"):
            self.log(f"using SOLRL_NITRO_AMI_ID: {ami_id}")
            return ami_id
        images = self.ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": [DEFAULT_AMI_NAME_FILTER]},
                {"Name": "architecture", "Values": ["x86_64"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
                {"Name": "state", "Values": ["available"]},
            ],
        ).get("Images", [])
        if not images:
            raise AwsNitroRunnerError("No Amazon Linux 2 Kernel 5.10 AMI found via DescribeImages")
        image = sorted(images, key=lambda item: item["CreationDate"], reverse=True)[0]
        self.log(f"Amazon Linux 2 Kernel 5.10 AMI from DescribeImages: {image['ImageId']}")
        return image["ImageId"]

    def _create_security_group(self, vpc_id: str) -> None:
        self.log("creating tagged security group with no ingress")
        response = self.ec2.create_security_group(
            GroupName=f"{self.config.name}-sg",
            Description=f"SolRL Nitro smoke {self.config.run_id}; no ingress",
            VpcId=vpc_id,
            TagSpecifications=tag_specifications(self.config, ["security-group"]),
        )
        self.security_group_id = response["GroupId"]
        self.log(f"security group: {self.security_group_id}")

    def _launch_instance(self, ami_id: str, subnet_id: str) -> None:
        self.log(f"launching tagged enclave-enabled EC2 parent ({self.config.instance_type})")
        user_data = remote_smoke_script(
            self.config,
            sha256_hex(f"{self.config.run_id}:solrl-claim-v1"),
            sha256_hex(f"{self.config.run_id}:worker-public-key"),
            sha256_hex(f"{self.config.run_id}:nonce"),
        )
        self._write_text("user-data.sh", user_data)
        response = self.ec2.run_instances(
            ImageId=ami_id,
            MinCount=1,
            MaxCount=1,
            InstanceType=self.config.instance_type,
            NetworkInterfaces=[
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet_id,
                    "Groups": [self.security_group_id],
                    "AssociatePublicIpAddress": True,
                }
            ],
            EnclaveOptions={"Enabled": True},
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            TagSpecifications=tag_specifications(self.config, ["instance", "volume", "network-interface"]),
            UserData=user_data,
        )
        self._write_json("run-instances.json", response)
        self.instance_id = response["Instances"][0]["InstanceId"]
        self.log(f"instance: {self.instance_id}")

    def _wait_for_instance(self) -> None:
        self.log("waiting for EC2 status checks")
        self.ec2.get_waiter("instance_status_ok").wait(
            InstanceIds=[self.instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": 80},
        )
        self.log("EC2 status checks passed")

    def _wait_for_console_smoke(self) -> str:
        self.log("waiting for SolRL markers in EC2 console output")
        deadline = time.time() + self.config.timeout_seconds
        last_output = ""
        while time.time() < deadline:
            response = self.ec2.get_console_output(InstanceId=self.instance_id, Latest=True)
            output = response.get("Output", "") or ""
            if output and output != last_output:
                last_output = output
                self._write_text("console-output.txt", output)
            if "SOLRL_REMOTE_FAILED=" in output:
                raise AwsNitroRunnerError("remote Nitro smoke failed; see artifacts console-output.txt")
            if "SOLRL_STATUS=OK" in output:
                return output
            time.sleep(15)
        self._write_text("console-output.txt", last_output)
        raise AwsNitroRunnerError("timed out waiting for SolRL markers in EC2 console output")

    def _persist_remote_outputs(self, markers: dict[str, str]) -> None:
        self._write_text("attestation.hex", markers["SOLRL_ATTESTATION_HEX"] + "\n")
        build_json = base64.b64decode(markers["SOLRL_BUILD_JSON_B64"])
        self._write_text("nitro-build.json", build_json.decode("utf-8"))
        self._write_json("remote-markers.json", {k: v for k, v in markers.items() if k != "SOLRL_ATTESTATION_HEX"})

    def cleanup(self) -> None:
        self.log(f"cleanup starting for run id {self.config.run_id}")
        if self.instance_id:
            if self._instance_tags_match():
                self.log(f"terminating tagged instance {self.instance_id}")
                self.ec2.terminate_instances(InstanceIds=[self.instance_id])
                self.ec2.get_waiter("instance_terminated").wait(
                    InstanceIds=[self.instance_id],
                    WaiterConfig={"Delay": 15, "MaxAttempts": 80},
                )
            else:
                self.log(f"refusing to terminate {self.instance_id}; SolRL tags do not match")
        if self.security_group_id:
            if self._security_group_tags_match():
                self.log(f"deleting tagged security group {self.security_group_id}")
                try:
                    self.ec2.delete_security_group(GroupId=self.security_group_id)
                except botocore.exceptions.ClientError as exc:
                    self.log(f"security group delete failed: {exc}")
            else:
                self.log(f"refusing to delete {self.security_group_id}; SolRL tags do not match")
        self.log("cleanup finished")

    def _instance_tags_match(self) -> bool:
        response = self.ec2.describe_instances(InstanceIds=[self.instance_id])
        tags = response["Reservations"][0]["Instances"][0].get("Tags", [])
        return tags_match(tags, self.config)

    def _security_group_tags_match(self) -> bool:
        response = self.ec2.describe_security_groups(GroupIds=[self.security_group_id])
        return tags_match(response["SecurityGroups"][0].get("Tags", []), self.config)

    def _write_json(self, name: str, value: Any) -> None:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.artifact_dir / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    def _write_text(self, name: str, value: str) -> None:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.config.artifact_dir / name).write_text(value, encoding="utf-8")


def remote_smoke_script(config: RunnerConfig, user_data_hex: str, public_key_hex: str, nonce_hex: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
exec > >(tee /var/log/solrl-nitro-smoke.log /dev/console) 2>&1
on_err() {{
  rc=$?
  echo SOLRL_REMOTE_FAILED=$rc
  systemctl status nitro-enclaves-allocator.service --no-pager -l || true
  journalctl -u nitro-enclaves-allocator.service --no-pager -n 120 || true
  tail -160 /tmp/solrl-docker-build.log 2>/dev/null || true
  exit $rc
}}
trap on_err ERR

yum update -y >/dev/null
amazon-linux-extras install aws-nitro-enclaves-cli -y >/dev/null
yum install -y aws-nitro-enclaves-cli-devel docker jq python3 >/dev/null
systemctl enable --now docker

install -d -m 0755 /etc/nitro_enclaves
cat >/etc/nitro_enclaves/allocator.yaml <<'YAML'
---
memory_mib: 1024
cpu_count: 2
YAML
systemctl enable nitro-enclaves-allocator.service >/dev/null
systemctl daemon-reload
systemctl restart nitro-enclaves-allocator.service
export NITRO_CLI_ARTIFACTS=/var/lib/solrl/nitro-artifacts
export NITRO_CLI_BLOBS=/usr/share/nitro_enclaves/blobs
mkdir -p "$NITRO_CLI_ARTIFACTS"
test -d "$NITRO_CLI_BLOBS"

WORK=/opt/solrl-nitro-worker
rm -rf "$WORK"
mkdir -p "$WORK/src"
cat >"$WORK/Cargo.toml" <<'TOML'
[package]
name = "solrl-nitro-worker"
version = "0.1.0"
edition = "2021"

[dependencies]
aws-nitro-enclaves-nsm-api = "0.4.0"
hex = "0.4.3"
libc = "0.2"
serde_bytes = "0.11"
TOML

cat >"$WORK/src/main.rs" <<'RS'
use aws_nitro_enclaves_nsm_api::{{
    api::{{Request, Response}},
    driver::{{nsm_exit, nsm_init, nsm_process_request}},
}};
use serde_bytes::ByteBuf;
use std::{{env, fs::File, io::Write, mem, os::fd::FromRawFd, ptr}};

fn env_hex(name: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {{
    Ok(hex::decode(env::var(name)?)?)
}}

fn serve_once(payload: &[u8], port: u32) -> Result<(), Box<dyn std::error::Error>> {{
    let fd = unsafe {{ libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM, 0) }};
    if fd < 0 {{
        return Err("failed to create vsock socket".into());
    }}
    let addr = libc::sockaddr_vm {{
        svm_family: libc::AF_VSOCK as libc::sa_family_t,
        svm_reserved1: 0,
        svm_port: port,
        svm_cid: u32::MAX,
        svm_zero: [0; 4],
    }};
    let rc = unsafe {{
        libc::bind(
            fd,
            &addr as *const libc::sockaddr_vm as *const libc::sockaddr,
            mem::size_of::<libc::sockaddr_vm>() as libc::socklen_t,
        )
    }};
    if rc != 0 {{
        return Err("failed to bind vsock listener".into());
    }}
    if unsafe {{ libc::listen(fd, 1) }} != 0 {{
        return Err("failed to listen on vsock".into());
    }}
    let client = unsafe {{ libc::accept(fd, ptr::null_mut(), ptr::null_mut()) }};
    if client < 0 {{
        return Err("failed to accept vsock client".into());
    }}
    let mut stream = unsafe {{ File::from_raw_fd(client) }};
    stream.write_all(payload)?;
    stream.write_all(b"\\n")?;
    unsafe {{ libc::close(fd) }};
    Ok(())
}}

fn main() -> Result<(), Box<dyn std::error::Error>> {{
    let user_data = env_hex("SOLRL_USER_DATA_HEX")?;
    let public_key = env_hex("SOLRL_PUBLIC_KEY_HEX")?;
    let nonce = env_hex("SOLRL_NONCE_HEX")?;
    let port: u32 = env::var("SOLRL_VSOCK_PORT")?.parse()?;
    let nsm_fd = nsm_init();
    if nsm_fd < 0 {{
        return Err("failed to initialize NSM".into());
    }}
    let pcr16 = match nsm_process_request(nsm_fd, Request::ExtendPCR {{ index: 16, data: user_data.clone() }}) {{
        Response::ExtendPCR {{ data }} => data,
        Response::Error(err) => {{
            nsm_exit(nsm_fd);
            return Err(format!("failed to extend PCR16: {{err:?}}").into());
        }}
        other => {{
            nsm_exit(nsm_fd);
            return Err(format!("unexpected ExtendPCR response: {{other:?}}").into());
        }}
    }};
    match nsm_process_request(nsm_fd, Request::LockPCR {{ index: 16 }}) {{
        Response::LockPCR => {{}}
        Response::Error(err) => {{
            nsm_exit(nsm_fd);
            return Err(format!("failed to lock PCR16: {{err:?}}").into());
        }}
        other => {{
            nsm_exit(nsm_fd);
            return Err(format!("unexpected LockPCR response: {{other:?}}").into());
        }}
    }};
    match nsm_process_request(nsm_fd, Request::DescribePCR {{ index: 16 }}) {{
        Response::DescribePCR {{ lock, data }} if lock && data == pcr16 => {{}}
        Response::DescribePCR {{ lock, data }} => {{
            nsm_exit(nsm_fd);
            return Err(format!("PCR16 lock check failed: lock={{lock}}, bytes={{}}", data.len()).into());
        }}
        Response::Error(err) => {{
            nsm_exit(nsm_fd);
            return Err(format!("failed to describe PCR16: {{err:?}}").into());
        }}
        other => {{
            nsm_exit(nsm_fd);
            return Err(format!("unexpected DescribePCR response: {{other:?}}").into());
        }}
    }};
    let response = nsm_process_request(
        nsm_fd,
        Request::Attestation {{
            public_key: Some(ByteBuf::from(public_key)),
            user_data: Some(ByteBuf::from(user_data)),
            nonce: Some(ByteBuf::from(nonce)),
        }},
    );
    nsm_exit(nsm_fd);
    let document = match response {{
        Response::Attestation {{ document }} => document,
        other => return Err(format!("unexpected NSM response: {{other:?}}").into()),
    }};
    serve_once(hex::encode(document).as_bytes(), port)
}}
RS

cat >"$WORK/Dockerfile" <<'DOCKER'
FROM rust:1.88-bookworm AS build
WORKDIR /src
COPY Cargo.toml .
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
ARG SOLRL_USER_DATA_HEX
ARG SOLRL_PUBLIC_KEY_HEX
ARG SOLRL_NONCE_HEX
ARG SOLRL_VSOCK_PORT
ENV SOLRL_USER_DATA_HEX=$SOLRL_USER_DATA_HEX
ENV SOLRL_PUBLIC_KEY_HEX=$SOLRL_PUBLIC_KEY_HEX
ENV SOLRL_NONCE_HEX=$SOLRL_NONCE_HEX
ENV SOLRL_VSOCK_PORT=$SOLRL_VSOCK_PORT
COPY --from=build /src/target/release/solrl-nitro-worker /solrl-nitro-worker
CMD ["/solrl-nitro-worker"]
DOCKER

docker build "$WORK" \\
  --build-arg SOLRL_USER_DATA_HEX={user_data_hex} \\
  --build-arg SOLRL_PUBLIC_KEY_HEX={public_key_hex} \\
  --build-arg SOLRL_NONCE_HEX={nonce_hex} \\
  --build-arg SOLRL_VSOCK_PORT={config.vsock_port} \\
  -t solrl-nitro-worker:{config.run_id} >/tmp/solrl-docker-build.log 2>&1

nitro-cli build-enclave --docker-uri solrl-nitro-worker:{config.run_id} --output-file /tmp/solrl-worker.eif \\
  >/tmp/solrl-build.json
nitro-cli run-enclave --cpu-count 2 --memory 512 --enclave-cid 16 --eif-path /tmp/solrl-worker.eif \\
  >/tmp/solrl-run.json
sleep 5
python3 - <<'PY' > /tmp/solrl-attestation.hex
import socket
import time

sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
for _ in range(90):
    try:
        sock.connect((16, {config.vsock_port}))
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("could not connect to SolRL worker enclave over VSOCK")

chunks = []
while True:
    data = sock.recv(65536)
    if not data:
        break
    chunks.append(data)
print(b"".join(chunks).decode("ascii").strip())
PY
nitro-cli describe-enclaves >/tmp/solrl-describe.json
ENCLAVE_ID="$(jq -r '.[0].EnclaveID // empty' /tmp/solrl-describe.json)"
test -n "$ENCLAVE_ID"
nitro-cli terminate-enclave --enclave-id "$ENCLAVE_ID" >/tmp/solrl-terminate.json

echo SOLRL_EXPECTED_USER_DATA_HEX={user_data_hex}
echo SOLRL_EXPECTED_PUBLIC_KEY_HEX={public_key_hex}
echo SOLRL_BUILD_JSON_B64="$(base64 -w0 /tmp/solrl-build.json)"
sleep 3
echo SOLRL_ATTESTATION_HEX_BEGIN
ATTESTATION_CHUNK_WIDTH=96
ATTESTATION_CHUNKS="$(fold -w "$ATTESTATION_CHUNK_WIDTH" /tmp/solrl-attestation.hex | wc -l)"
echo SOLRL_ATTESTATION_HEX_WIDTH="$ATTESTATION_CHUNK_WIDTH"
echo SOLRL_ATTESTATION_HEX_CHUNKS="$ATTESTATION_CHUNKS"
for pass in 1 2; do
    echo SOLRL_ATTESTATION_HEX_PASS="$pass"
    fold -w "$ATTESTATION_CHUNK_WIDTH" /tmp/solrl-attestation.hex | awk '{{ printf "SOLRL_ATTESTATION_HEX_CHUNK=%04d:%s\\n", NR - 1, $0 }}'
done
echo SOLRL_ATTESTATION_HEX_END
echo SOLRL_STATUS=OK
"""


def run_smoke(args: argparse.Namespace) -> int:
    require_docker()
    load_dotenv()
    normalise_aws_env()
    config = make_config(args.run_id, Path(args.artifact_root))
    session = boto3.Session(region_name=config.region)
    runner = AwsNitroRunner(config, session)
    runner.log(f"run id: {config.run_id}")
    runner.log(f"region: {config.region}")
    runner.log(f"artifact dir: {config.artifact_dir}")
    runner.smoke()
    print(f"SolRL AWS Nitro smoke OK: {config.artifact_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SolRL AWS Nitro runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="run the real AWS Nitro attestation smoke")
    smoke.add_argument("--run-id", default=os.environ.get("SOLRL_RUN_ID"))
    smoke.add_argument("--artifact-root", default=os.environ.get("SOLRL_AWS_ARTIFACT_ROOT", "artifacts/aws-nitro"))
    smoke.set_defaults(func=run_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AwsNitroRunnerError as exc:
        print(f"aws-nitro-runner failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
