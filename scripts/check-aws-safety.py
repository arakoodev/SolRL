#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "python/solrl_core/aws_nitro_runner.py"
TEMPLATE_DIR = ROOT / "python/solrl_core/aws_nitro_templates"
E2E = ROOT / "scripts/e2e-aws-nitro.sh"
SIDECAR = ROOT / "scripts/aws-nitro-smoke.sh"
PREFLIGHT_SIDECAR = ROOT / "scripts/_preflight_readonly.py"
POSTAUDIT_SIDECAR = ROOT / "scripts/_postaudit_readonly.py"
WORKER = ROOT / "crates/solrl-nitro-worker/src/main.rs"
WORKER_CARGO = ROOT / "crates/solrl-nitro-worker/Cargo.toml"
FLAKE = ROOT / "flake.nix"
WORKFLOW = ROOT / ".github/workflows/build-nitro-eif.yml"
ACTRC = ROOT / ".actrc"
REMOTE_PYTHON_FILES = (
    ROOT / "python/solrl_core/aws_nitro_runner.py",
    ROOT / "python/solrl_core/claim.py",
    ROOT / "python/solrl_core/claim_context.py",
    ROOT / "python/solrl_core/config.py",
)


def fail(message: str) -> None:
    print(f"aws safety lint failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(text: str, needle: str, message: str) -> None:
    if needle not in text:
        fail(message)


def require_python37_parse(path: Path) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 7))
    except SyntaxError as exc:
        fail(f"{path.relative_to(ROOT)} must parse on Amazon Linux 2 Python 3.7: line {exc.lineno}: {exc.msg}")


def main() -> int:
    if SIDECAR.exists():
        fail("scripts/aws-nitro-smoke.sh must not exist; AWS smoke must use the main runner path")
    if PREFLIGHT_SIDECAR.exists() or POSTAUDIT_SIDECAR.exists():
        fail("AWS audits must be first-class aws_nitro_runner subcommands, not sidecar scripts")

    runner = RUNNER.read_text(encoding="utf-8")
    templates = "\n".join(path.read_text(encoding="utf-8") for path in sorted(TEMPLATE_DIR.glob("*")))
    remote_path = TEMPLATE_DIR / "remote_smoke.sh.tpl"
    if (
        not remote_path.exists()
        or not WORKER.exists()
        or not WORKER_CARGO.exists()
        or not FLAKE.exists()
        or not WORKFLOW.exists()
        or not ACTRC.exists()
    ):
        fail("AWS Nitro runner must keep remote template, worker crate, flake source, GitHub EIF workflow, and act config")
    e2e = E2E.read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    remote = remote_path.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    worker_cargo = WORKER_CARGO.read_text(encoding="utf-8")
    flake = FLAKE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    combined_runner = runner + "\n" + templates

    for path in REMOTE_PYTHON_FILES:
        require_python37_parse(path)

    require(e2e, "python3 -m solrl_core.aws_nitro_runner smoke", "e2e-aws-nitro.sh must call the main runner module")
    require(runner, "def require_docker(", "AWS runner must refuse host execution")
    require(runner, "SOLRL_IN_DOCKER", "AWS runner must check SOLRL_IN_DOCKER")
    require(runner, "Project", "AWS runner must define Project tag")
    require(runner, "SolRLRunId", "AWS runner must define run-id tag")
    require(runner, "ManagedBy", "AWS runner must define ManagedBy tag")
    require(runner, "TagSpecifications=tag_specifications", "EC2 resources must use tag specifications")
    require(runner, "tags_match(", "cleanup must be gated on ownership tags")
    require(runner, "EnclaveOptions={\"Enabled\": True}", "EC2 parent must launch with Nitro Enclaves enabled")
    require(runner, "AssociatePublicIpAddress", "runner must make egress explicit for default subnets")
    require(runner, "BlockDeviceMappings=", "AWS runner must size the temporary root volume explicitly")
    require(runner, "\"DeleteOnTermination\": True", "AWS runner root volume must be deleted with the instance")
    require(runner, "SOLRL_NITRO_ROOT_VOLUME_GIB", "AWS runner must allow explicit root volume override")
    require(runner, "SOLRL_NITRO_AMI_ID", "AWS runner must allow an explicit AMI override")
    require(runner, "describe_images(", "AWS runner must resolve the AMI through ec2:DescribeImages")
    require(runner, "UserData=user_data", "AWS runner must execute the remote smoke through EC2 user-data")
    require(runner, "get_console_output(", "AWS runner must collect the final smoke result through EC2 console output")
    require(
        runner,
        "CONSOLE_RESULT_POLL_ATTEMPTS = 60",
        "AWS runner must poll EC2 console for 10 minutes after stopped state; AWS propagation is delayed",
    )
    require(
        runner,
        "for latest in (True, False):",
        "AWS runner must read both Latest and non-Latest EC2 console output variants",
    )
    require(runner, "audit_resources(", "AWS runner must include read-only resource audits")
    require(runner, "_preflight_audit(", "smoke must run a first-class preflight audit")
    require(runner, "_post_audit(", "smoke must run a first-class post-run audit")
    require(runner, "def run_cleanup(", "main AWS runner must expose exact-run cleanup")
    require(runner, "cleanup_run_resources(", "cleanup must use the shared tag-gated cleanup path")
    require(runner, "cleanup requires --run-id", "cleanup must require an explicit run id")
    require(runner, "allow_existing_solrl", "preflight blocking must require an explicit override")
    require(runner, "Preflight found existing active/stopped Project=SolRL resources", "preflight must block by default")
    require(runner, "Post-audit found resources left over", "post-audit must fail on exact run leftovers")
    require(runner, "MAX_EC2_USER_DATA_BYTES", "runner must guard EC2 user-data size")
    require(runner, "resolve_git_source(", "AWS runner must resolve the public git source for EC2 source verification")
    require(runner, "resolve_eif_artifact_source(", "AWS runner must resolve the GHCR EIF artifact source")
    require(runner, "SOLRL_NITRO_EIF_OCI", "AWS runner must allow an explicit EIF OCI artifact override")
    require(runner, "derive_ghcr_eif_ref(", "AWS runner must derive the default EIF artifact from the git commit")
    require(runner, "refusing real AWS Nitro smoke from a dirty worktree", "AWS runner must not test stale pushed code")
    require(runner, 'status", "--porcelain"', "AWS runner must check worktree cleanliness before deriving default git ref")
    require(runner, "40-character commit SHA", "AWS runner must reject branch refs for default EIF artifacts")
    require(runner, "public_clone_url(", "AWS runner must convert GitHub SSH remotes to public HTTPS clone URLs")
    require(runner, "verify-attestation", "EC2 parent must verify the Nitro attestation before printing OK")
    require(
        runner,
        "build_claim_context(",
        "AWS smoke must derive Nitro user_data from the canonical ClaimV1 context builder",
    )
    require(
        runner,
        "claim_context[\"pcr16_user_data\"]",
        "AWS smoke must send PCR16 user_data derived from Pcr16Components, not run-id filler",
    )
    require(
        runner,
        "compute_artifacts[\"trajectory_hash\"]",
        "AWS smoke must bind attestation user_data to the generic compute output used as ClaimV1 trajectory_hash",
    )
    require(
        runner,
        "claim_context[\"pcr16\"]",
        "AWS smoke must compare the Nitro PCR16 to the registry ClaimV1 PCR16",
    )
    require(
        runner,
        "remote tail:",
        "AWS runner must surface compact remote failure tails instead of hiding failed SOLRL_RESULT blocks",
    )
    if ":=" in runner:
        fail("AWS runner must stay Python 3.7 compatible for the Amazon Linux 2 EC2 parent; walrus operator is forbidden")
    if "strict=False" in runner:
        fail("AWS runner must stay Python 3.7 compatible; zip(strict=...) is forbidden")
    if ".removeprefix(" in runner:
        fail("AWS runner must stay Python 3.7 compatible; str.removeprefix is forbidden")
    if ".not_valid_before_utc" in runner or ".not_valid_after_utc" in runner:
        fail("AWS runner must support cryptography<42 on the EC2 parent; use compatibility accessors")
    if "from solrl_core.config import load_config" in runner.split("class AwsNitroRunner", 1)[0]:
        fail("verify-attestation must import on EC2 without tomli; load_config must not be a top-level runner import")
    if 'sha256_hex(f"{self.config.run_id}:solrl-claim-v1")' in runner:
        fail("AWS smoke must not use run-id filler as Nitro user_data")
    require(
        combined_runner,
        "cat >/etc/nitro_enclaves/allocator.yaml",
        "AWS runner must write Nitro allocator config in the main user-data path",
    )
    require(
        combined_runner,
        "systemctl daemon-reload",
        "AWS runner must reload systemd before restarting the Nitro allocator",
    )
    if remote.index("systemctl restart nitro-enclaves-allocator.service") > remote.index("nitro-cli run-enclave"):
        fail("AWS remote smoke must start the Nitro allocator before run-enclave")
    require(
        remote,
        "exec >\"$LOG_DIR/user-data.log\" 2>&1",
        "AWS remote smoke must keep noisy logs out of EC2 console output",
    )
    if "tee /var/log" in remote or "/dev/console) 2>&1" in remote:
        fail("AWS remote smoke must not tee the whole user-data stream to the EC2 console")
    require(remote, "SOLRL_RESULT_BEGIN", "AWS remote smoke must print one final result block")
    require(remote, "SOLRL_RESULT_END", "AWS remote smoke must close the final result block")
    require(remote, "SOLRL_PHASE_START=$PHASE", "AWS remote smoke must print bounded phase diagnostics")
    require(remote, "SOLRL_PHASE_END=$PHASE", "AWS remote smoke must print phase completion diagnostics")
    require(remote, "SOLRL_PHASE_FAILED=$PHASE", "AWS remote smoke must print phase failure diagnostics")
    require(remote, "SOLRL_PHASE_EXIT=", "AWS remote smoke must print phase exit status diagnostics")
    require(remote, "SOLRL_PHASE_TIMEOUT_SECONDS=$timeout_seconds", "AWS remote smoke must print phase timeout diagnostics")
    require(remote, "SOLRL_STATUS=OK", "AWS remote smoke must print OK only in the final result block")
    require(remote, "SOLRL_STATUS=FAILED", "AWS remote smoke must print compact failure markers")
    require(remote, "tail -80", "AWS remote smoke failure output must be capped")
    require(remote, "shutdown -h now", "AWS remote smoke must stop the instance after final result emission")
    require(remote, "flush_console", "AWS remote smoke must flush console output before shutdown")
    require(remote, "sleep 10", "AWS remote smoke must give EC2 console output time to persist before shutdown")
    require(remote, "write_console_file", "AWS remote smoke must write result files to serial console")
    require(remote, "/dev/ttyS0", "AWS remote smoke must write diagnostics directly to the serial tty")
    require(remote, "OVERALL_TIMEOUT_SECONDS=1800", "AWS remote smoke must have a hard overall watchdog")
    require(remote, "start_watchdog", "AWS remote smoke must start the hard watchdog")
    require(remote, "SOLRL_PHASE=overall_timeout", "AWS remote watchdog must emit a typed timeout failure")
    require(remote, "/run/solrl.done", "AWS remote smoke must mark completion for the watchdog")
    require(remote, "/run/solrl.phase", "AWS remote smoke must track the current phase for timeout diagnostics")
    require(remote, "kill -TERM \"$phase_pid\"", "AWS remote smoke must terminate timed-out phases")
    require(remote, "kill -KILL \"$phase_pid\"", "AWS remote smoke must force-kill stuck phases")
    require(remote, "run_phase packages 600 phase_packages", "AWS remote smoke must timeout package setup")
    require(remote, "run_phase oras 300 phase_oras", "AWS remote smoke must timeout ORAS setup")
    require(remote, "run_phase pull_eif 900 phase_pull_eif", "AWS remote smoke must timeout EIF artifact pulls")
    require(remote, "run_phase attestation 300 phase_attestation", "AWS remote smoke must timeout VSOCK attestation")
    require(
        remote,
        "no running enclaves",
        "AWS remote smoke must treat an already-exited one-shot enclave as successful cleanup",
    )
    if 'test -n "$enclave_id"' in remote:
        fail("AWS terminate_enclave phase must not fail when the one-shot enclave already exited")
    require(remote, "export HOME=/root", "AWS remote smoke must set HOME under cloud-init")
    require(remote, "git clone \"$git_url\" \"$SRC_DIR\"", "AWS remote smoke must clone the public repo on EC2")
    require(remote, "git checkout --detach", "AWS remote smoke must checkout an explicit immutable git ref")
    require(remote, "oras pull \"$eif_ref\" --output \"$EIF_DIR\"", "AWS remote smoke must pull the CI-built EIF through ORAS")
    require(remote, "sha384sum -c \"$sha_path\"", "AWS remote smoke must verify the pulled EIF checksum")
    require(remote, "SOLRL_EIF_OCI_REF", "AWS remote smoke must report the EIF OCI reference it booted")
    require(remote, "'cryptography<42'", "AWS remote smoke must pin Python deps for Amazon Linux 2 Python compatibility")
    require(remote, "nitro-cli describe-eif", "AWS remote smoke must persist EIF measurements")
    require(remote, "sha384sum", "AWS remote smoke must persist EIF hash evidence")
    require(remote, "sock.sendall(payload.encode(\"ascii\"))", "attestation inputs must arrive over VSOCK at runtime")
    require(flake, "nitro.buildEif", "SolRL flake must build an EIF through aws-nitro-util")
    require(flake, "oysterPkgs.kernels.vanilla", "SolRL EIF must use the pinned Marlin/Oyster kernel path")
    require(
        flake,
        "pkgs.pkgsStatic.rustPlatform.buildRustPackage",
        "SolRL Nitro worker must be built as a static Rust binary to keep the EIF runtime root small",
    )
    require(flake, "copyToRoot = app;", "SolRL Nitro EIF must copy only the app root into the runtime image")
    require(
        flake,
        "solrl-nitro-worker-root = app;",
        "SolRL flake must expose the worker root as a cacheable CI stage",
    )
    require(
        flake,
        "solrl-nitro-kernel-bundle = oysterPkgs.kernels.vanilla.default;",
        "SolRL flake must expose the Marlin/Oyster kernel bundle as a cacheable CI stage",
    )
    if "pkgs.busybox" in flake:
        fail("SolRL Nitro worker EIF must not include busybox; the entrypoint is a binary, not a shell script")
    if 'hex = "0.4.3"' in worker_cargo or "hex::" in worker:
        fail("SolRL Nitro worker must not pull the hex crate into the EIF for small fixed hex parsing/encoding")
    for release_flag in (
        "[profile.release]",
        "lto = true",
        'opt-level = "z"',
        'panic = "abort"',
        'strip = "symbols"',
    ):
        require(worker_cargo, release_flag, f"SolRL Nitro worker release profile must keep {release_flag}")
    require(workflow, "nix build .#solrl-nitro-worker-eif", "GitHub Actions must build the EIF with Nix")
    require(
        workflow,
        "DeterminateSystems/magic-nix-cache-action@v",
        "GitHub Actions must use a pinned Nix cache action for EIF builds",
    )
    require(
        workflow,
        'diagnostic-endpoint: ""',
        "GitHub Actions Nix cache must disable diagnostics for this repo workflow",
    )
    require(
        workflow,
        "nix build .#solrl-nitro-worker --no-write-lock-file --no-link",
        "GitHub Actions must stage the static worker build for cache reuse",
    )
    require(
        workflow,
        "nix build .#solrl-nitro-kernel-bundle --no-write-lock-file --no-link",
        "GitHub Actions must stage the Nitro kernel bundle build for cache reuse",
    )
    require(
        workflow,
        "nix build .#solrl-nitro-worker-root --no-write-lock-file --no-link",
        "GitHub Actions must stage the app root build for cache reuse",
    )
    require(workflow, "application/vnd.aws.nitro.eif", "GitHub Actions must publish the raw EIF as an OCI artifact")
    require(workflow, "oras push", "GitHub Actions must publish the EIF with ORAS")
    require(workflow, "packages: write", "GitHub Actions must declare package write permission")
    require(workflow, "id-token: write", "GitHub Actions must declare OIDC permission for the Nix installer action")
    require(workflow, "DeterminateSystems/nix-installer-action@v", "GitHub Actions must pin the Nix installer to a version tag")
    for floating_action in ("DeterminateSystems/nix-installer-action@main", "DeterminateSystems/magic-nix-cache-action@main"):
        if floating_action in workflow:
            fail(f"GitHub Actions must not float Actions on @main: found {floating_action}")
    for request in ("ExtendPCR", "LockPCR", "DescribePCR"):
        if not re.search(rf"Request::{request}\s*\{{[^}}]*\bindex:\s*16\b", worker, re.DOTALL):
            fail(f"AWS worker must issue Request::{request} against PCR16")
    require(remote, "verify-attestation", "AWS remote smoke must run the EC2-side attestation verifier")
    require(remote, "--expected-pcr16-hex", "AWS remote smoke must pass the registry ClaimV1 PCR16 to attestation verify")
    require(
        remote,
        "--expected-pcr16-user-data-hex",
        "AWS remote smoke must verify PCR16 against ClaimV1 pcr16_user_data separately from attestation user_data",
    )
    require(remote, "PCR16_USER_DATA_HEX", "AWS worker request must pass PCR16 extension input explicitly")
    require(remote, "COMPUTE_INPUT_HEX", "AWS worker request must pass the generic compute input")
    require(remote, "OUTPUT_HASH_HEX", "AWS remote smoke must parse the worker's generic compute output")
    require(remote, "ATTESTATION_HEX", "AWS remote smoke must parse the worker's attestation separately")
    require(
        remote,
        "attestation_document_hash",
        "AWS remote smoke must publish a 32-byte hash linking ClaimV1 to the verified raw Nitro document",
    )
    require(
        runner,
        "nitro-claim-receipt.json",
        "AWS runner must write a ClaimV1 receipt tied to the real Nitro attestation document hash",
    )
    require(remote, "SOLRL_COMPUTE_OUTPUT_HASH", "AWS remote smoke must print the generic compute output hash")
    require(remote, "SOLRL_CLAIM_PCR16", "AWS remote smoke must print the registry ClaimV1 PCR16 beside Nitro PCR16")
    require(
        remote,
        "SOLRL_CLAIM_CONTEXT_HASH",
        "AWS remote smoke must print the ClaimV1 context hash used to derive Nitro user_data",
    )
    if remote.index("verify-attestation") > remote.index("SOLRL_STATUS=OK"):
        fail("AWS remote smoke must verify attestation before printing OK")
    require(runner, "aws_nitro_templates", "AWS runner must render the remote smoke from source templates")
    require(runner, "instance_running", "AWS runner must not block on long EC2 status checks before polling cloud-init")
    require(runner, "_capture_console_snapshot", "AWS runner must capture best-effort console output on failure")
    require(runner, "botocore.exceptions.WaiterError", "AWS runner must wrap waiter failures as typed runner errors")
    if "instance_status_ok" in runner:
        fail("AWS runner must not wait for full instance_status_ok before reading cloud-init output")
    if "__SOURCE_TARBALL_B64__" in remote:
        fail("AWS remote smoke must not ship a source tarball to the EC2 parent")
    if "create_bucket(" in combined_runner or "generate_presigned_url" in combined_runner or "upload_file(" in combined_runner:
        fail("AWS Nitro smoke must not use S3 or presigned URLs for this no-S3 path")
    if "docker build" in remote:
        fail("AWS Nitro smoke must not build the EIF through Docker")
    if "nitro-cli build-enclave" in remote:
        fail("AWS Nitro smoke must not use non-reproducible nitro-cli build-enclave")
    if "nixos.org/nix/install" in remote or "nix build .#solrl-nitro-worker-eif" in remote:
        fail("AWS remote smoke must not rebuild the EIF on the EC2 hot path")
    if "oras login" in remote:
        fail("AWS remote smoke must not require registry credentials on the EC2 parent")
    if "--build-arg SOLRL_USER_DATA_HEX" in combined_runner:
        fail("run-specific attestation inputs must not be baked into the EIF image")
    if "systemctl enable --now nitro-enclaves-allocator.service" in combined_runner:
        fail("Nitro allocator must not be started before allocator.yaml exists")
    for forbidden_marker in (
        "SOLRL_ATTESTATION_HEX",
        "SOLRL_BUILD_JSON_B64",
        "SOLRL_ATTESTATION_HEX_CHUNK",
        "SOLRL_BUILD_JSON_B64_CHUNK",
    ):
        if forbidden_marker in remote:
            fail(f"AWS runner must not move bulk artifacts through EC2 console output: found {forbidden_marker}")

    if "authorize_security_group_ingress" in combined_runner:
        fail("AWS runner must not add inbound security group rules")
    if "KeyName" in combined_runner:
        fail("AWS runner must not create or use SSH key pairs")
    if "--debug-mode" in combined_runner:
        fail("AWS runner must not use debug-mode for attestation smoke")
    for forbidden in (
        "client(\"iam\")",
        "client('iam')",
        "create_role",
        "create_instance_profile",
        "IamInstanceProfile",
        "PassRole",
        "send_command",
        "get_command_invocation",
        "describe_instance_information",
    ):
        if forbidden in combined_runner:
            fail(f"AWS runner default path must not depend on IAM or SSM: found {forbidden}")
    if "env_file:" in compose and ".env" in compose:
        fail("docker-compose.yml must not load .env through env_file; the runner reads it without exposing secrets")
    aws_runner_block = compose.split("aws-nitro-runner:", 1)[1].split("\n  harbor-runner:", 1)[0]
    for forbidden_env in ("AWS_ACCESS_KEY_ID: test", "AWS_SECRET_ACCESS_KEY: test", "LOCALSTACK_ENDPOINT:"):
        if forbidden_env in aws_runner_block:
            fail(f"aws-nitro-runner must not inherit LocalStack dummy AWS config: found {forbidden_env}")
    for forbidden_compose in ("SYS_ADMIN", "seccomp=unconfined", "privileged: true", "nix-cache:/nix"):
        if forbidden_compose in aws_runner_block:
            fail(f"aws-nitro-runner Docker service should not need local build privileges: found {forbidden_compose}")
    for required_validation in ("validate_remote_hex", "validate_vsock_port", "validate_remote_token", "validate_git_source"):
        require(runner, required_validation, f"AWS runner template renderer must validate {required_validation}")
    if "dt.UTC" in runner:
        fail("AWS runner must stay Python 3.10-compatible inside dev-shell; use dt.timezone.utc")

    print("aws safety lint OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
