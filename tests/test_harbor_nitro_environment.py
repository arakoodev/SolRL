from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from solrl_harbor.nitro_environment import NitroEnvironment, NitroEnvironmentError


async def _exercise_environment(tmp_path: Path) -> None:
    env = NitroEnvironment(
        environment_dir=tmp_path,
        environment_name="pytest-task",
        session_id="trial-1",
        trial_paths=None,
        task_env_config=None,
        solrl_root=tmp_path / "harbor-root",
    )

    with pytest.raises(NitroEnvironmentError, match="not been started"):
        await env.exec("true")

    await env.start()
    source = tmp_path / "input.txt"
    source.write_text("hello", encoding="utf-8")
    await env.upload_file(source, "/work/input.txt")

    result = await env.exec("cat input.txt && printf world > output.txt", cwd="/work")

    assert result.return_code == 0
    assert result.stdout == "hello"
    assert await env.is_file("/work/output.txt")
    out = tmp_path / "output.txt"
    await env.download_file("/work/output.txt", out)
    assert out.read_text(encoding="utf-8") == "world"

    await env.stop(delete=True)
    assert not (tmp_path / "harbor-root" / "trial-1").exists()


def test_nitro_environment_local_transport(tmp_path: Path) -> None:
    asyncio.run(_exercise_environment(tmp_path))


def test_nitro_environment_rejects_unwired_aws_mode(tmp_path: Path) -> None:
    with pytest.raises(NitroEnvironmentError, match="not wired yet"):
        NitroEnvironment(
            environment_dir=tmp_path,
            environment_name="pytest-task",
            session_id="trial-1",
            trial_paths=None,
            task_env_config=None,
            solrl_root=tmp_path / "harbor-root",
            solrl_mode="aws",
        )
