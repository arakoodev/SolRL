from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


try:  # pragma: no cover - covered when Harbor is installed in the runner image.
    from harbor.environments.base import BaseEnvironment, ExecResult
    from harbor.models.environment_type import EnvironmentType
except ImportError:  # pragma: no cover - exercised by repo unit tests without Harbor installed.

    @dataclass
    class ExecResult:  # type: ignore[no-redef]
        stdout: str | None = None
        stderr: str | None = None
        return_code: int = 0

    class BaseEnvironment:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.environment_dir = Path(kwargs.get("environment_dir", "."))
            self.environment_name = kwargs.get("environment_name", "solrl")
            self.session_id = kwargs.get("session_id", "local")
            self.task_env_config = kwargs.get("task_env_config")
            self.default_user = None

    class EnvironmentType:  # type: ignore[no-redef]
        DOCKER = "docker"


class NitroEnvironmentError(RuntimeError):
    pass


class NitroEnvironment(BaseEnvironment):
    """Harbor import-path environment for SolRL's Nitro execution lane.

    V1 exposes a local deterministic transport that matches Harbor's environment
    interface while the production AWS path is hardened separately. The important
    thing for now is not to fork Harbor. Harbor can instantiate this class via:

        solrl_harbor.nitro_environment:NitroEnvironment
    """

    @staticmethod
    def type() -> EnvironmentType:
        return EnvironmentType.DOCKER

    @property
    def is_mounted(self) -> bool:
        return False

    @property
    def supports_gpus(self) -> bool:
        return False

    @property
    def can_disable_internet(self) -> bool:
        return True

    @classmethod
    def preflight(cls) -> None:
        if os.environ.get("SOLRL_IN_DOCKER") != "1":
            raise SystemExit("SolRL Harbor environment must run inside Docker. Use docker compose.")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.mode = kwargs.pop("solrl_mode", os.environ.get("SOLRL_HARBOR_NITRO_MODE", "local"))
        self.root = Path(kwargs.pop("solrl_root", os.environ.get("SOLRL_HARBOR_ROOT", "artifacts/harbor-nitro")))
        self._validate_definition()
        super().__init__(*args, **kwargs)
        self.workspace = self.root / str(self.session_id)
        self.started = False

    def _validate_definition(self) -> None:
        if self.mode not in {"local", "aws"}:
            raise NitroEnvironmentError(f"unsupported SolRL Harbor mode: {self.mode}")
        if self.mode == "aws":
            raise NitroEnvironmentError(
                "AWS Harbor Nitro execution is not wired yet. Use local mode or the dedicated real Nitro smoke."
            )

    async def start(self, force_build: bool = False) -> None:
        del force_build
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.started = True

    async def stop(self, delete: bool = False) -> None:
        self.started = False
        if delete:
            shutil.rmtree(self.workspace, ignore_errors=True)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self._require_started()
        target = self._remote_path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source_path), target)

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        self._require_started()
        target = self._remote_path(target_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(Path(source_dir), target)

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        self._require_started()
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._remote_path(source_path), target)

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        self._require_started()
        source = self._remote_path(source_dir)
        target = Path(target_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        del user
        self._require_started()
        run_cwd = self._remote_path(cwd or ".")
        run_cwd.mkdir(parents=True, exist_ok=True)
        merged_env = {**os.environ, **(env or {})}

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=run_cwd,
                env=merged_env,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(stdout=exc.stdout, stderr=exc.stderr or "command timed out", return_code=124)

        return ExecResult(stdout=proc.stdout, stderr=proc.stderr, return_code=proc.returncode)

    async def is_dir(self, path: str, user: str | int | None = None) -> bool:
        del user
        return self._remote_path(path).is_dir()

    async def is_file(self, path: str, user: str | int | None = None) -> bool:
        del user
        return self._remote_path(path).is_file()

    def _require_started(self) -> None:
        if not self.started:
            raise NitroEnvironmentError("SolRL Harbor environment has not been started")

    def _remote_path(self, path: str | Path) -> Path:
        path = Path(path)
        if path.is_absolute():
            path = Path(*path.parts[1:])
        return self.workspace / path
