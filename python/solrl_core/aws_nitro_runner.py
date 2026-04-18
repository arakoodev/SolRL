from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
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
DEFAULT_ROOT_VOLUME_GIB = 64
DEFAULT_VSOCK_PORT = 5005
MAX_EC2_USER_DATA_BYTES = 16_384
ACTIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped"]
ROOT = Path(__file__).resolve().parents[2]
FINAL_RESULT_REQUIRED_FIELDS = {
    "SOLRL_STATUS",
    "SOLRL_RUN_ID",
    "SOLRL_GIT_REF",
    "SOLRL_EIF_SHA384",
    "SOLRL_NITRO_ROOT_SHA256",
    "SOLRL_PCR0",
    "SOLRL_PCR1",
    "SOLRL_PCR2",
    "SOLRL_PCR16",
}


class AwsNitroRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerConfig:
    region: str
    run_id: str
    name: str
    instance_type: str
    artifact_dir: Path
    root_volume_gib: int = DEFAULT_ROOT_VOLUME_GIB
    vsock_port: int = DEFAULT_VSOCK_PORT
    timeout_seconds: int = 7200
    allow_existing_solrl: bool = False


@dataclass(frozen=True)
class DecodedAttestation:
    timestamp_ms: int
    pcrs: dict[int, bytes]
    public_key: bytes
    user_data: bytes
    root_public_key: bytes

    def to_summary(self) -> dict[str, Any]:
        root_sha256 = hashlib.sha256(self.root_public_key).hexdigest()
        return {
            "timestamp_ms": self.timestamp_ms,
            "pcrs": {str(k): v.hex() for k, v in sorted(self.pcrs.items())},
            "public_key": self.public_key.hex(),
            "user_data": self.user_data.hex(),
            "root_public_key": self.root_public_key.hex(),
            "root_public_key_sha256": root_sha256,
        }


@dataclass(frozen=True)
class GitSource:
    url: str
    ref: str


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


def make_config(
    run_id: str | None = None,
    artifact_root: Path = Path("artifacts/aws-nitro"),
    allow_existing_solrl: bool = False,
) -> RunnerConfig:
    generated_run_id = run_id or f"solrl-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
    name = f"SolRL-{generated_run_id}"
    return RunnerConfig(
        region=os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION,
        run_id=generated_run_id,
        name=name,
        instance_type=os.environ.get("SOLRL_NITRO_INSTANCE_TYPE", DEFAULT_INSTANCE_TYPE),
        artifact_dir=artifact_root / generated_run_id,
        root_volume_gib=int(os.environ.get("SOLRL_NITRO_ROOT_VOLUME_GIB", str(DEFAULT_ROOT_VOLUME_GIB))),
        allow_existing_solrl=allow_existing_solrl,
    )


def tag_value(tags: list[dict[str, str]], key: str) -> str:
    for tag in tags:
        if tag.get("Key") == key:
            return tag.get("Value", "")
    return ""


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


def audit_resources(ec2: Any, run_id: str | None = None) -> dict[str, Any]:
    tag_filter = (
        {"Name": "tag:SolRLRunId", "Values": [run_id]}
        if run_id
        else {"Name": "tag:Project", "Values": ["SolRL"]}
    )
    reservations = ec2.describe_instances(
        Filters=[tag_filter, {"Name": "instance-state-name", "Values": ACTIVE_INSTANCE_STATES}]
    ).get("Reservations", [])
    instances = [
        {
            "id": instance["InstanceId"],
            "state": instance["State"]["Name"],
            "run_id": tag_value(instance.get("Tags", []), "SolRLRunId"),
            "name": tag_value(instance.get("Tags", []), "Name"),
        }
        for reservation in reservations
        for instance in reservation.get("Instances", [])
    ]
    security_groups = [
        {
            "id": group["GroupId"],
            "run_id": tag_value(group.get("Tags", []), "SolRLRunId"),
            "name": tag_value(group.get("Tags", []), "Name"),
        }
        for group in ec2.describe_security_groups(Filters=[tag_filter]).get("SecurityGroups", [])
    ]
    volumes = [
        {
            "id": volume["VolumeId"],
            "state": volume["State"],
            "run_id": tag_value(volume.get("Tags", []), "SolRLRunId"),
            "name": tag_value(volume.get("Tags", []), "Name"),
        }
        for volume in ec2.describe_volumes(Filters=[tag_filter]).get("Volumes", [])
    ]
    return {
        "scope": {"run_id": run_id} if run_id else {"project": "SolRL"},
        "instances": instances,
        "security_groups": security_groups,
        "volumes": volumes,
    }


def audit_counts(audit: dict[str, Any]) -> dict[str, int]:
    return {
        "instances": len(audit["instances"]),
        "security_groups": len(audit["security_groups"]),
        "volumes": len(audit["volumes"]),
    }


def audit_has_resources(audit: dict[str, Any]) -> bool:
    return any(audit_counts(audit).values())


def audit_lines(audit: dict[str, Any]) -> list[str]:
    counts = audit_counts(audit)
    scope = audit["scope"]
    prefix = f"RUN[{scope['run_id']}]" if "run_id" in scope else "PROJECT[SolRL]"
    lines = [
        f"{prefix}_ACTIVE_OR_STOPPED_INSTANCES={counts['instances']}",
        f"{prefix}_SECURITY_GROUPS={counts['security_groups']}",
        f"{prefix}_VOLUMES={counts['volumes']}",
    ]
    for instance in audit["instances"]:
        lines.append(
            f"INSTANCE id={instance['id']} state={instance['state']} "
            f"run_id={instance['run_id']} name={instance['name']}"
        )
    for group in audit["security_groups"]:
        lines.append(f"SECURITY_GROUP id={group['id']} run_id={group['run_id']} name={group['name']}")
    for volume in audit["volumes"]:
        lines.append(
            f"VOLUME id={volume['id']} state={volume['state']} "
            f"run_id={volume['run_id']} name={volume['name']}"
        )
    return lines


def _resource_tags_by_id(ec2: Any, resource_type: str, resource_id: str) -> list[dict[str, str]]:
    if resource_type == "instance":
        response = ec2.describe_instances(InstanceIds=[resource_id])
        reservations = response.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            return []
        return reservations[0]["Instances"][0].get("Tags", [])
    if resource_type == "security-group":
        response = ec2.describe_security_groups(GroupIds=[resource_id])
        groups = response.get("SecurityGroups", [])
        return groups[0].get("Tags", []) if groups else []
    if resource_type == "volume":
        response = ec2.describe_volumes(VolumeIds=[resource_id])
        volumes = response.get("Volumes", [])
        return volumes[0].get("Tags", []) if volumes else []
    raise AwsNitroRunnerError(f"unsupported cleanup resource type: {resource_type}")


def cleanup_run_resources(ec2: Any, config: RunnerConfig, log: Any = print) -> dict[str, Any]:
    audit = audit_resources(ec2, config.run_id)
    terminated_instances: list[str] = []
    deleted_security_groups: list[str] = []
    deleted_volumes: list[str] = []

    for instance in audit["instances"]:
        instance_id = instance["id"]
        tags = _resource_tags_by_id(ec2, "instance", instance_id)
        if not tags_match(tags, config):
            log(f"cleanup refusing instance {instance_id}; SolRL tags do not match")
            continue
        log(f"cleanup terminating tagged instance {instance_id}")
        ec2.terminate_instances(InstanceIds=[instance_id])
        terminated_instances.append(instance_id)
    if terminated_instances:
        ec2.get_waiter("instance_terminated").wait(
            InstanceIds=terminated_instances,
            WaiterConfig={"Delay": 15, "MaxAttempts": 80},
        )

    for group in audit["security_groups"]:
        group_id = group["id"]
        tags = _resource_tags_by_id(ec2, "security-group", group_id)
        if not tags_match(tags, config):
            log(f"cleanup refusing security group {group_id}; SolRL tags do not match")
            continue
        log(f"cleanup deleting tagged security group {group_id}")
        for attempt in range(12):
            try:
                ec2.delete_security_group(GroupId=group_id)
                deleted_security_groups.append(group_id)
                break
            except botocore.exceptions.ClientError as exc:
                error = exc.response.get("Error", {})
                if error.get("Code") != "DependencyViolation" or attempt == 11:
                    raise
                time.sleep(5)

    refreshed = audit_resources(ec2, config.run_id)
    for volume in refreshed["volumes"]:
        volume_id = volume["id"]
        if volume["state"] != "available":
            log(f"cleanup leaving volume {volume_id}; state is {volume['state']}")
            continue
        tags = _resource_tags_by_id(ec2, "volume", volume_id)
        if not tags_match(tags, config):
            log(f"cleanup refusing volume {volume_id}; SolRL tags do not match")
            continue
        log(f"cleanup deleting tagged available volume {volume_id}")
        ec2.delete_volume(VolumeId=volume_id)
        deleted_volumes.append(volume_id)

    return {
        "terminated_instances": terminated_instances,
        "deleted_security_groups": deleted_security_groups,
        "deleted_volumes": deleted_volumes,
        "remaining": audit_resources(ec2, config.run_id),
    }


def parse_remote_markers(stdout: str) -> dict[str, str]:
    begin = stdout.rfind("SOLRL_RESULT_BEGIN")
    end = stdout.rfind("SOLRL_RESULT_END")
    if begin < 0 or end < 0 or end < begin:
        raise AwsNitroRunnerError("remote Nitro command did not return a final SOLRL_RESULT block")

    markers: dict[str, str] = {}
    block = stdout[begin:end].splitlines()
    for line in block:
        if line.startswith("SOLRL_") and "=" in line:
            marker = line.split("SOLRL_", 1)[1]
            key_suffix, value = marker.split("=", 1)
            key = f"SOLRL_{key_suffix}"
            markers[key] = value.strip()

    missing = sorted({"SOLRL_STATUS"} - markers.keys())
    if missing:
        raise AwsNitroRunnerError(f"remote Nitro command did not return markers: {', '.join(missing)}")
    if markers["SOLRL_STATUS"] == "FAILED":
        phase = markers.get("SOLRL_PHASE", "unknown")
        raise AwsNitroRunnerError(f"remote Nitro smoke failed during phase {phase}; see console-output.txt")
    if markers["SOLRL_STATUS"] != "OK":
        raise AwsNitroRunnerError(f"remote Nitro command returned status {markers['SOLRL_STATUS']}")

    missing = sorted(FINAL_RESULT_REQUIRED_FIELDS - markers.keys())
    if missing:
        raise AwsNitroRunnerError(f"remote Nitro OK result is missing markers: {', '.join(missing)}")
    for key in ("SOLRL_EIF_SHA384", "SOLRL_PCR0", "SOLRL_PCR1", "SOLRL_PCR2", "SOLRL_PCR16"):
        value = markers[key]
        if len(value) != 96 or any(char not in "0123456789abcdefABCDEF" for char in value):
            raise AwsNitroRunnerError(f"remote Nitro marker {key} must be 48-byte hex")
    root_sha = markers["SOLRL_NITRO_ROOT_SHA256"]
    if len(root_sha) != 64 or any(char not in "0123456789abcdefABCDEF" for char in root_sha):
        raise AwsNitroRunnerError("remote Nitro root key hash marker must be 32-byte hex")
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


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise AwsNitroRunnerError(f"could not resolve git source with git {' '.join(args)}") from exc


def public_clone_url(remote_url: str) -> str:
    if remote_url.startswith("git@github.com:"):
        return "https://github.com/" + remote_url.removeprefix("git@github.com:")
    if remote_url.startswith("ssh://git@github.com/"):
        return "https://github.com/" + remote_url.removeprefix("ssh://git@github.com/")
    return remote_url


def validate_git_source(source: GitSource) -> None:
    parsed = urllib.parse.urlparse(source.url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AwsNitroRunnerError("SOLRL_NITRO_GIT_URL must be a public HTTPS clone URL without credentials")
    if not source.ref or not all(char.isalnum() or char in "._/-" for char in source.ref):
        raise AwsNitroRunnerError("SOLRL_NITRO_GIT_REF must contain only letters, numbers, slash, dot, underscore, or dash")


def resolve_git_source() -> GitSource:
    url = os.environ.get("SOLRL_NITRO_GIT_URL") or public_clone_url(_git_output("remote", "get-url", "origin"))
    ref = os.environ.get("SOLRL_NITRO_GIT_REF") or _git_output("rev-parse", "HEAD")
    source = GitSource(url=url, ref=ref)
    validate_git_source(source)
    return source


class AwsNitroRunner:
    def __init__(self, config: RunnerConfig, session: boto3.Session) -> None:
        self.config = config
        self.ec2 = session.client("ec2")
        self.sts = session.client("sts")
        self.account_id = ""
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
        post_audit = False
        try:
            self._preflight_audit()
            post_audit = True
            self._smoke()
        except BaseException as exc:
            run_error = exc
        else:
            run_error = None
        finally:
            if self.instance_id or self.security_group_id:
                self.cleanup()
            if post_audit:
                self._post_audit()
        if run_error is not None:
            if isinstance(run_error, botocore.exceptions.ClientError):
                error = run_error.response.get("Error", {})
                code = error.get("Code", "Unknown")
                message = error.get("Message", str(run_error))
                raise AwsNitroRunnerError(f"AWS API failed with {code}: {message}") from run_error
            raise run_error

    def _preflight_audit(self) -> None:
        audit = audit_resources(self.ec2)
        self._write_json("preflight-project-audit.json", audit)
        for line in audit_lines(audit):
            self.log(f"preflight {line}")
        if audit_has_resources(audit) and not self.config.allow_existing_solrl:
            raise AwsNitroRunnerError(
                "Preflight found existing active/stopped Project=SolRL resources. "
                "Refusing to launch in a shared AWS account. Pass --allow-existing-solrl to override."
            )

    def _post_audit(self) -> None:
        run_audit = audit_resources(self.ec2, self.config.run_id)
        project_audit = audit_resources(self.ec2)
        self._write_json("postaudit-run.json", run_audit)
        self._write_json("postaudit-project.json", project_audit)
        for line in audit_lines(run_audit):
            self.log(f"postaudit {line}")
        for line in audit_lines(project_audit):
            self.log(f"postaudit {line}")
        if audit_has_resources(run_audit):
            raise AwsNitroRunnerError("Post-audit found resources left over for this exact SolRLRunId")
        if audit_has_resources(project_audit) and not self.config.allow_existing_solrl:
            raise AwsNitroRunnerError("Post-audit found active/stopped Project=SolRL resources after cleanup")

    def _smoke(self) -> None:
        caller = self.sts.get_caller_identity()
        self.account_id = caller["Account"]
        self._write_json("caller.json", {"Account": caller["Account"], "Arn": caller["Arn"]})
        self.log(f"authenticated AWS account: {caller['Account']}")
        git_source = resolve_git_source()
        self._write_json("git-source.json", {"url": git_source.url, "ref": git_source.ref})
        self.log(f"remote EC2 build source: {git_source.url}@{git_source.ref}")

        vpc_id, subnet_id = self._default_network()
        ami_id = self._default_ami()
        self._create_security_group(vpc_id)
        self._launch_instance(ami_id, subnet_id, git_source)
        self._wait_for_instance()
        stdout = self._wait_for_final_console_result()
        markers = parse_remote_markers(stdout)
        self._persist_remote_outputs(markers)
        self.log("remote EC2 verified Nitro attestation and printed final result")

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

    def _launch_instance(self, ami_id: str, subnet_id: str, git_source: GitSource) -> None:
        root_device_name = self._ami_root_device_name(ami_id)
        self.log(
            f"launching tagged enclave-enabled EC2 parent ({self.config.instance_type}, "
            f"{self.config.root_volume_gib} GiB root)"
        )
        user_data = remote_smoke_script(
            self.config,
            sha256_hex(f"{self.config.run_id}:solrl-claim-v1"),
            sha256_hex(f"{self.config.run_id}:worker-public-key"),
            sha256_hex(f"{self.config.run_id}:nonce"),
            git_source,
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
            InstanceInitiatedShutdownBehavior="stop",
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            BlockDeviceMappings=[
                {
                    "DeviceName": root_device_name,
                    "Ebs": {
                        "VolumeSize": self.config.root_volume_gib,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=tag_specifications(self.config, ["instance", "volume", "network-interface"]),
            UserData=user_data,
        )
        self._write_json("run-instances.json", response)
        self.instance_id = response["Instances"][0]["InstanceId"]
        self.log(f"instance: {self.instance_id}")

    def _ami_root_device_name(self, ami_id: str) -> str:
        images = self.ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if not images:
            raise AwsNitroRunnerError(f"Unable to describe AMI root device for {ami_id}")
        return images[0].get("RootDeviceName") or "/dev/xvda"

    def _wait_for_instance(self) -> None:
        self.log("waiting for EC2 status checks")
        self.ec2.get_waiter("instance_status_ok").wait(
            InstanceIds=[self.instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": 80},
        )
        self.log("EC2 status checks passed")

    def _wait_for_final_console_result(self) -> str:
        self.log("waiting for EC2 user-data to finish and stop the instance")
        deadline = time.time() + self.config.timeout_seconds
        while time.time() < deadline:
            response = self.ec2.describe_instances(InstanceIds=[self.instance_id])
            reservations = response.get("Reservations", [])
            instances = [item for reservation in reservations for item in reservation.get("Instances", [])]
            if not instances:
                raise AwsNitroRunnerError(f"instance {self.instance_id} disappeared before final console result")
            state = instances[0]["State"]["Name"]
            if state in {"stopped", "terminated"}:
                break
            time.sleep(15)
        else:
            raise AwsNitroRunnerError("timed out waiting for EC2 instance to finish Nitro smoke")

        self.log("instance finished; reading final EC2 console output")
        last_output = ""
        for _attempt in range(12):
            response = self.ec2.get_console_output(InstanceId=self.instance_id, Latest=True)
            output = response.get("Output", "") or ""
            if output:
                last_output = output
                self._write_text("console-output.txt", output)
                if "SOLRL_RESULT_BEGIN" in output and "SOLRL_RESULT_END" in output:
                    return output
            time.sleep(10)
        self._write_text("console-output.txt", last_output)
        raise AwsNitroRunnerError("remote Nitro smoke did not publish a final SOLRL_RESULT block")

    def _persist_remote_outputs(self, markers: dict[str, str]) -> None:
        self._write_json("remote-markers.json", markers)
        self._write_text("eif-sha384.txt", markers["SOLRL_EIF_SHA384"] + "\n")

    def cleanup(self) -> None:
        self.log(f"cleanup starting for run id {self.config.run_id}")
        cleanup_run_resources(self.ec2, self.config, self.log)
        self.log("cleanup finished")

    def _write_json(self, name: str, value: Any) -> None:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.artifact_dir / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    def _write_text(self, name: str, value: str) -> None:
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.config.artifact_dir / name).write_text(value, encoding="utf-8")


TEMPLATE_DIR = Path(__file__).with_name("aws_nitro_templates")


def template_text(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def validate_remote_token(name: str, value: str) -> None:
    if not value or not all(char.isalnum() or char in "._-" for char in value):
        raise AwsNitroRunnerError(f"{name} must contain only letters, numbers, dot, underscore, or dash")


def validate_remote_ref(name: str, value: str) -> None:
    if not value or not all(char.isalnum() or char in "._/-" for char in value):
        raise AwsNitroRunnerError(f"{name} must contain only letters, numbers, slash, dot, underscore, or dash")


def validate_remote_hex(name: str, value: str) -> None:
    if not value or len(value) % 2 != 0 or not all(char in "0123456789abcdefABCDEF" for char in value):
        raise AwsNitroRunnerError(f"{name} must be non-empty even-length hex")


def validate_vsock_port(port: int) -> None:
    if port < 1 or port > 65_535:
        raise AwsNitroRunnerError("vsock_port must be between 1 and 65535")


def remote_smoke_script(
    config: RunnerConfig,
    user_data_hex: str,
    public_key_hex: str,
    nonce_hex: str,
    git_source: GitSource,
) -> str:
    validate_remote_token("run_id", config.run_id)
    validate_remote_hex("user_data_hex", user_data_hex)
    validate_remote_hex("public_key_hex", public_key_hex)
    validate_remote_hex("nonce_hex", nonce_hex)
    validate_git_source(git_source)
    validate_remote_ref("git_ref", git_source.ref)
    validate_vsock_port(config.vsock_port)
    replacements = {
        "__GIT_URL_B64__": base64.b64encode(git_source.url.encode("utf-8")).decode("ascii"),
        "__GIT_REF__": git_source.ref,
        "__USER_DATA_HEX__": user_data_hex,
        "__PUBLIC_KEY_HEX__": public_key_hex,
        "__NONCE_HEX__": nonce_hex,
        "__VSOCK_PORT__": str(config.vsock_port),
        "__RUN_ID__": config.run_id,
    }
    script = template_text("remote_smoke.sh.tpl")
    for token, value in replacements.items():
        script = script.replace(token, value)
    unresolved = [token for token in replacements if token in script]
    if unresolved:
        raise AwsNitroRunnerError(f"remote Nitro template has unresolved tokens: {', '.join(unresolved)}")
    size = len(script.encode("utf-8"))
    if size > MAX_EC2_USER_DATA_BYTES:
        raise AwsNitroRunnerError(
            f"remote Nitro user-data is {size} bytes; EC2 limit is {MAX_EC2_USER_DATA_BYTES} bytes"
        )
    return script


def run_verify_attestation(args: argparse.Namespace) -> int:
    document = Path(args.attestation_hex_path).read_text(encoding="utf-8").strip()
    decoded = verify_attestation_document(
        bytes.fromhex(document),
        bytes.fromhex(args.expected_user_data_hex),
        bytes.fromhex(args.expected_public_key_hex),
    )
    summary = decoded.to_summary()
    Path(args.summary_json).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def run_smoke(args: argparse.Namespace) -> int:
    require_docker()
    load_dotenv()
    normalise_aws_env()
    config = make_config(args.run_id, Path(args.artifact_root), allow_existing_solrl=args.allow_existing_solrl)
    session = boto3.Session(region_name=config.region)
    runner = AwsNitroRunner(config, session)
    runner.log(f"run id: {config.run_id}")
    runner.log(f"region: {config.region}")
    runner.log(f"artifact dir: {config.artifact_dir}")
    runner.smoke()
    print(f"SolRL AWS Nitro smoke OK: {config.artifact_dir}")
    return 0


def run_audit(args: argparse.Namespace) -> int:
    require_docker()
    load_dotenv()
    normalise_aws_env()
    session = boto3.Session(region_name=os.environ["AWS_DEFAULT_REGION"])
    caller = session.client("sts").get_caller_identity()
    print(f"AWS_ACCOUNT={caller['Account']}")
    print(f"AWS_ARN={caller['Arn']}")
    audit = audit_resources(session.client("ec2"), args.run_id)
    for line in audit_lines(audit):
        print(line)
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    require_docker()
    load_dotenv()
    normalise_aws_env()
    if not args.run_id:
        raise AwsNitroRunnerError("cleanup requires --run-id")
    config = make_config(args.run_id, Path(args.artifact_root), allow_existing_solrl=True)
    session = boto3.Session(region_name=config.region)
    caller = session.client("sts").get_caller_identity()
    print(f"AWS_ACCOUNT={caller['Account']}")
    print(f"AWS_ARN={caller['Arn']}")
    ec2 = session.client("ec2")
    before = audit_resources(ec2, config.run_id)
    for line in audit_lines(before):
        print(f"before {line}")
    result = cleanup_run_resources(ec2, config)
    print(json.dumps(result, indent=2, sort_keys=True))
    after = result["remaining"]
    for line in audit_lines(after):
        print(f"after {line}")
    if audit_has_resources(after):
        raise AwsNitroRunnerError("cleanup finished with exact run resources still present")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SolRL AWS Nitro runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-attestation", help=argparse.SUPPRESS)
    verify.add_argument("--attestation-hex-path", required=True)
    verify.add_argument("--expected-user-data-hex", required=True)
    verify.add_argument("--expected-public-key-hex", required=True)
    verify.add_argument("--summary-json", required=True)
    verify.set_defaults(func=run_verify_attestation)
    smoke = subparsers.add_parser("smoke", help="run the real AWS Nitro attestation smoke")
    smoke.add_argument("--run-id", default=os.environ.get("SOLRL_RUN_ID"))
    smoke.add_argument("--artifact-root", default=os.environ.get("SOLRL_AWS_ARTIFACT_ROOT", "artifacts/aws-nitro"))
    smoke.add_argument(
        "--allow-existing-solrl",
        action="store_true",
        help="allow launch when other active/stopped Project=SolRL resources already exist",
    )
    smoke.set_defaults(func=run_smoke)
    audit = subparsers.add_parser("audit", help="read-only audit of SolRL-tagged AWS resources")
    audit.add_argument("--scope", choices=["project"], default="project")
    audit.add_argument("--run-id")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=run_audit)
    cleanup = subparsers.add_parser("cleanup", help="delete only resources tagged with an exact SolRL run id")
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--artifact-root", default=os.environ.get("SOLRL_AWS_ARTIFACT_ROOT", "artifacts/aws-nitro"))
    cleanup.set_defaults(func=run_cleanup)
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
