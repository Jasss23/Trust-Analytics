# Pluang Multi-Agent Analytics Reporting System

Take-home prototype for Pluang's AI Engineer case study. The project implements a runnable Python CLI pipeline with a SQL Agent, Quality Agent, and human-in-the-loop review step over the provided CSV data loaded into SQLite.

## Architecture

```text
CSV files + dbt metadata
  -> pluang-agent setup
  -> SQLite
  -> SQL Agent
  -> Quality Agent
  -> Human Review
  -> approve | one-time reinvestigation | audit_required
```

The workflow is orchestrated with LangGraph. The SQL Agent is guarded: it can use OpenRouter when `--prefer-llm` and `OPENROUTER_API_KEY` are provided, but the default path uses deterministic baseline queries so the evaluator can run the project without spending API credits. This also gives the system a stable oracle for tests.

The Quality Agent is deliberately separate from the SQL Agent. It receives only the SQL Agent answer and database access for cross-checks; it does not share hidden agent state. Human review is a real pipeline step with structured categories and retry limits.

## Context and Prompt Strategy

The SQL Agent context is built from:

- SQLite schema inspection.
- dbt source/model metadata in `_sources.yml` and `_models.yml`.
- Metric hints from the case brief.
- The original business question and optional reviewer retry note.

WrenAI influenced the semantic-context framing: expose source meaning, metric definitions, and ambiguity instead of only table/column names. Elementary influenced the data-quality posture: deterministic checks first, then source reconciliation and grounded hypotheses. Neither project is used as a dependency.

Important source assumptions:

- GTV and transaction count default to completed transactions.
- USD GTV uses recorded `amount_usd`, not an IDR conversion.
- `agg_monthly_biz_summary.mtu` is the primary business-summary MTU, but MTU is ambiguous because raw completed traders and Mixpanel MTU differ.
- `mart_ops_dashboard` is useful for QA cross-checks, but it includes non-failed transactions and fixed-rate USD conversion.

## Model Choice

Default model: `openai/gpt-4.1-mini` through OpenRouter.

This is cheap enough for a $5 key and good enough for structured JSON/text-to-SQL when surrounded by schema context, SQL guardrails, and deterministic QA. The model can be changed with `OPENROUTER_MODEL` without changing the workflow.

Live LLM calls are opt-in:

```bash
pluang-agent run --prefer-llm
```

Without `--prefer-llm`, the system uses deterministic SQL Agent answers and still runs the full Quality Agent and human review flow.

## How to Run

Create the environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set `.env`:

```bash
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-4.1-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PLUANG_DATA_DIR=/path/to/pluang_analytics_agent/data
PLUANG_DB_PATH=var/pluang.sqlite
```

Load data:

```bash
pluang-agent setup --data-dir "$PLUANG_DATA_DIR" --db-path "$PLUANG_DB_PATH"
```

Run approval demo:

```bash
pluang-agent run --review-mode demo-approve --db-path "$PLUANG_DB_PATH" --data-dir "$PLUANG_DATA_DIR"
```

Run rejection/reinvestigation demo:

```bash
pluang-agent run --review-mode demo-reject --db-path "$PLUANG_DB_PATH" --data-dir "$PLUANG_DATA_DIR"
```

Run interactive review:

```bash
pluang-agent run --review-mode interactive --db-path "$PLUANG_DB_PATH" --data-dir "$PLUANG_DATA_DIR"
```

Check OpenRouter key credit:

```bash
pluang-agent cost
```

## Sample Outputs

Committed sample artifacts are in `outputs/sample/`:

- `sql_agent_answers.json`
- `quality_report.json`
- `review_approval.log`
- `review_rejection_reinvestigation.log`

The rejection demo rejects the October-December GTV trend with category `source_wrong` and a reviewer note. The workflow performs one reinvestigation and records terminal state `reinvestigated`.

## Scaling

The current implementation works for seven tables because compact schema and dbt metadata fit comfortably in context. Across hundreds of dbt models, the first failures would be prompt size, source ambiguity, and competing metric definitions.

I would redesign the context layer as:

- dbt manifest/catalog ingestion rather than direct YAML snippets.
- semantic index over model descriptions, column descriptions, metrics, owners, and lineage.
- retrieval step that selects candidate models before SQL generation.
- source-ranking policy that prefers certified marts for business metrics and raw tables for reconciliation.
- explicit ambiguity output when multiple valid metric definitions remain.

This is where a WrenAI-style semantic layer would become more valuable than the lightweight prompt context used here.

## Testing

Run:

```bash
pytest
ruff check .
```

Current coverage includes:

- CSV loader idempotency and missing-file errors.
- SQLite read-only SQL guardrails.
- Deterministic baseline answers for the five required questions.
- Real-data checks for October USD GTV, MTU ambiguity, and October-December GTV trend.
- Quality Agent flags for MTU ambiguity and December monthly-summary disagreement.
- Scaffold checks for README sections and CLI entrypoint.

Something that did not work as expected: the first QA implementation flagged the first month in a month-on-month trend because `LAG()` naturally returns null for the initial month. I changed the generic null rule so expected analytic-window nulls are not treated as required metric failures.

## Cost

The committed demo outputs were generated with deterministic SQL Agent mode, so the OpenRouter spend for those runs was `$0.00`.

When live LLM mode is used, each OpenRouter request includes `usage: {"include": true}` so token usage and cost can be recorded on the SQL Agent answer. The project also includes `pluang-agent cost` to query remaining key credit.

Token-saving choices:

- Deterministic QA rules instead of asking the model to rediscover obvious data-quality checks.
- Compact dbt/schema context instead of full file dumps.
- One repair/retry path rather than unconstrained multi-turn SQL debugging.

## Limitations

The prototype is intentionally small. In production, the Quality Agent would still miss:

- upstream freshness problems not visible in the loaded CSVs,
- undocumented metric-definition changes,
- business events that explain a spike or drop but are absent from data,
- subtle joins or grain mismatches across many models,
- permission and row-level security concerns,
- statistically plausible but business-impossible values.

With more time, I would prioritize a richer semantic/lineage layer, metric certification, query provenance storage, historical anomaly baselines, and stronger reviewer UX for comparing conflicting sources side by side.

