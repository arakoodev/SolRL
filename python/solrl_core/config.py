from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised in Python 3.10 dev-shell
    import tomli as tomllib
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path("solrl.toml")


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)
