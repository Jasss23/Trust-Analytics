"""Command-line entrypoint for the Pluang analytics agent prototype."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pluang_agent.agents.quality_agent import QualityAgent
from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.config import load_settings
from pluang_agent.data_loader import DataLoadError, load_csvs
from pluang_agent.llm import LLMError, OpenRouterClient
from pluang_agent.metadata import case_root_from_data_dir, load_dbt_metadata
from pluang_agent.models import ReviewMode
from pluang_agent.questions import REQUIRED_QUESTIONS
from pluang_agent.workflow import run_pipeline, write_pipeline_outputs

app = typer.Typer(
    help="Pluang multi-agent analytics reporting prototype.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def setup(
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Directory containing the provided Pluang CSV files.",
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="SQLite database path to create or replace idempotently.",
    ),
) -> None:
    """Prepare the local SQLite database from provided CSVs."""
    settings = load_settings()
    resolved_data_dir = data_dir or settings.data_dir
    resolved_db_path = db_path or settings.db_path
    try:
        result = load_csvs(resolved_data_dir, resolved_db_path)
    except DataLoadError as exc:
        raise typer.BadParameter(str(exc)) from exc

    table = Table(title="Loaded SQLite Tables")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for name, count in sorted(result.row_counts.items()):
        table.add_row(name, str(count))
    console.print(table)
    console.print(f"[green]Database ready:[/green] {result.db_path}")


@app.command()
def run(
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="SQLite database path for the analytics pipeline.",
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Directory containing the provided Pluang CSV files; used to locate dbt metadata.",
    ),
    review_mode: ReviewMode = typer.Option(
        ReviewMode.INTERACTIVE,
        "--review-mode",
        help="Human review mode: interactive, demo-approve, or demo-reject.",
    ),
    output_dir: Path = typer.Option(
        Path("outputs/sample"),
        "--output-dir",
        help="Directory for JSON/log outputs.",
    ),
    prefer_llm: bool = typer.Option(
        False,
        "--prefer-llm",
        help="Use OpenRouter for SQL generation when OPENROUTER_API_KEY is set.",
    ),
) -> None:
    """Run the end-to-end agent pipeline."""
    settings = load_settings()
    resolved_db_path = db_path or settings.db_path
    resolved_data_dir = data_dir or settings.data_dir
    if not resolved_db_path.exists():
        raise typer.BadParameter(
            f"SQLite DB does not exist at {resolved_db_path}. Run `pluang-agent setup` first."
        )

    metadata = load_dbt_metadata(case_root_from_data_dir(resolved_data_dir))
    llm_client = OpenRouterClient(settings)
    sql_agent = SQLAgent(
        db_path=resolved_db_path,
        metadata=metadata,
        llm_client=llm_client,
        prefer_llm=prefer_llm,
    )
    quality_agent = QualityAgent(resolved_db_path)
    result = run_pipeline(REQUIRED_QUESTIONS, sql_agent, quality_agent, review_mode)
    write_pipeline_outputs(result, output_dir)

    table = Table(title="Pipeline Result")
    table.add_column("Question")
    table.add_column("Review")
    table.add_column("Terminal")
    table.add_column("QA Flags", justify="right")
    for item in result.items:
        decision = item.review_decision
        table.add_row(
            item.question.id,
            decision.decision if decision else "missing",
            decision.terminal_state.value if decision and decision.terminal_state else "unknown",
            str(len(item.quality_report.flags)),
        )
    console.print(table)
    console.print(f"[green]Outputs written to:[/green] {output_dir}")


@app.command("review-demo")
def review_demo(
    db_path: Path | None = typer.Option(None, "--db-path"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    output_dir: Path = typer.Option(Path("outputs/sample"), "--output-dir"),
) -> None:
    """Demonstrate the reserved human-review command shape."""
    run(
        db_path=db_path,
        data_dir=data_dir,
        review_mode=ReviewMode.DEMO_REJECT,
        output_dir=output_dir,
        prefer_llm=False,
    )


@app.command()
def cost() -> None:
    """Report OpenRouter usage and remaining credit."""
    settings = load_settings()
    client = OpenRouterClient(settings)
    try:
        credit = client.key_credit()
    except LLMError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=credit)


if __name__ == "__main__":
    app()
