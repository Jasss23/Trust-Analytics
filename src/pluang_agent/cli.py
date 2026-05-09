"""Command-line entrypoint for the Pluang analytics agent prototype."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(
    help="Pluang multi-agent analytics reporting prototype.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def setup(
    data_dir: Path = typer.Option(
        Path("data"),
        "--data-dir",
        help="Directory containing the provided Pluang CSV files.",
    ),
    db_path: Path = typer.Option(
        Path("var/pluang.sqlite"),
        "--db-path",
        help="SQLite database path to create or replace idempotently.",
    ),
) -> None:
    """Prepare the local SQLite database from provided CSVs."""
    console.print("[yellow]Setup command is scaffolded; CSV loading is not implemented yet.[/yellow]")
    console.print(f"Data directory: {data_dir}")
    console.print(f"Database path: {db_path}")


@app.command()
def run(
    db_path: Path = typer.Option(
        Path("var/pluang.sqlite"),
        "--db-path",
        help="SQLite database path for the analytics pipeline.",
    ),
    review_mode: str = typer.Option(
        "interactive",
        "--review-mode",
        help="Human review mode: interactive, demo-approve, or demo-reject.",
    ),
) -> None:
    """Run the end-to-end agent pipeline."""
    console.print("[yellow]Run command is scaffolded; agent workflow is not implemented yet.[/yellow]")
    console.print(f"Database path: {db_path}")
    console.print(f"Review mode: {review_mode}")


@app.command("review-demo")
def review_demo() -> None:
    """Demonstrate the reserved human-review command shape."""
    console.print("[yellow]Review demo is scaffolded; review state machine is not implemented yet.[/yellow]")


@app.command()
def cost() -> None:
    """Report OpenRouter usage and remaining credit."""
    console.print("[yellow]Cost command is scaffolded; OpenRouter lookup is not implemented yet.[/yellow]")


if __name__ == "__main__":
    app()

