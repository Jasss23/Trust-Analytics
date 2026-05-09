# Pluang Multi-Agent Analytics Reporting System

Take-home prototype for an AI Engineer role at Pluang. This repository will implement a LangGraph-based CLI pipeline with a SQL Agent, Quality Agent, and human-in-the-loop review step.

## Architecture

Planned flow:

```text
Business Questions
  -> SQL Agent
  -> Quality Agent
  -> Human Review
  -> approve | reinvestigate once | audit_required
```

The SQL Agent will answer questions against a local SQLite database built from the provided CSVs. The Quality Agent will validate answers through deterministic checks and cross-source reconciliation. Human review will pause the workflow and route structured reviewer feedback.

## Context and Prompt Strategy

The system will build context from dbt model metadata, source definitions, SQLite schema inspection, and lightweight data profiling. WrenAI is a useful reference for semantic/context-layer design, but this project will implement a smaller purpose-built context layer rather than depending on WrenAI.

## Model Choice

Default model: `openai/gpt-4.1-mini` through OpenRouter.

The MVP optimizes for low cost and predictable evaluator runs. The model is configurable through `OPENROUTER_MODEL` so a stronger reasoning model can be used later without changing orchestration code.

## How to Run

Create a local environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`, then point `PLUANG_DATA_DIR` to the extracted case-study `data/` directory.

Reserved commands:

```bash
pluang-agent setup --data-dir "$PLUANG_DATA_DIR" --db-path "$PLUANG_DB_PATH"
pluang-agent run --db-path "$PLUANG_DB_PATH"
pluang-agent review-demo
pluang-agent cost
```

The commands are scaffolded first; agent behavior will be implemented in later iterations.

## Scaling

For seven tables, the prototype can include compact schema and metric context directly. For hundreds of dbt models, the context layer should move toward model selection, semantic indexing, metric-definition retrieval, lineage-aware source ranking, and query-time context compression.

The first thing that breaks in a naive approach is prompt size and source ambiguity: the SQL Agent would see too many tables and too many competing definitions. The redesign should retrieve only relevant models, include lineage and ownership metadata, and require the agent to state unresolved ambiguity instead of silently choosing.

## Testing

Planned checks:

- Unit tests for configuration, setup idempotency, SQL safety, context loading, QA rules, and review routing.
- Integration tests with mocked OpenRouter responses.
- Optional live smoke test when `OPENROUTER_API_KEY` is present.

Current scaffold tests verify that the package and CLI entrypoints are present.

## Cost

The implementation will request OpenRouter usage details with each LLM call and report estimated spend. The default model is intentionally low cost, and deterministic validation will reduce unnecessary LLM retries.

## Limitations

This is a prototype, not a production analytics platform. The Quality Agent will likely miss subtle metric-definition drift, delayed pipeline freshness, hidden upstream transformations, and business context that is not represented in available data or dbt metadata.

