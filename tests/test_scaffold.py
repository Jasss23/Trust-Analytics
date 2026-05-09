from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_script_entrypoint_is_registered() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["pluang-agent"] == "pluang_agent.cli:app"


def test_required_readme_sections_exist() -> None:
    readme = (ROOT / "README.md").read_text()

    for heading in [
        "## Architecture",
        "## Context and Prompt Strategy",
        "## Model Choice",
        "## How to Run",
        "## Scaling",
        "## Testing",
        "## Cost",
        "## Limitations",
    ]:
        assert heading in readme


def test_local_secret_files_are_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text()

    for pattern in [".env", ".venv/", "data/", "var/", "*.sqlite", "*.db"]:
        assert pattern in gitignore

