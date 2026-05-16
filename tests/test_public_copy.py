from __future__ import annotations

from pathlib import Path

PUBLIC_PATHS = [
    Path("README.md"),
    Path("AGENTS.md"),
    Path("pyproject.toml"),
    Path(".env.example"),
    Path("setup.sh"),
    Path("Makefile"),
    Path("web/index.html"),
    Path("web/assets/app.js"),
    Path("web/assets/styles.css"),
    Path("deploy/gcp-cloud-run.md"),
]


def test_public_surface_is_not_case_study_branded() -> None:
    banned = [
        "P" + "luang",
        "p" + "luang",
        "case" + " study",
        "take" + "-home",
        "take" + " home",
        "Open" + "Router",
        "OPEN" + "ROUTER",
        "P" + "LUANG",
    ]
    for path in PUBLIC_PATHS:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{token!r} leaked into {path}"
