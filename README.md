# Pluang Multi-Agent Analytics Reporting System

Take-home submission for the Pluang Machine Learning Engineer (Agents & Data Intelligence) case study.

The system answers business questions about platform performance, validates each answer through a structured Quality Agent, and surfaces both to a human reviewer with enough context to act. It is a working prototype, not a production system — but the engineering decisions are deliberate and (per the brief's principle) "a clean system with clear reasoning is stronger than an over-engineered one that produces noise."

---

## Architecture

```
                 ┌────────────────┐
question ─────▶ │   SQL Agent    │ ─────▶ SQLAgentAnswer
                 │   (LLM)        │       (source provenance, sql,
                 └────────┬───────┘        interpretation_choices,
                          │                dq_notes, ...)
                          ▼
                 ┌────────────────┐
                 │ Quality Agent  │
                 │  Layer A: rules│ ◀── detects null / zero / negative /
                 │                │      empty / out-of-range
                 │  Layer B: rules│ ◀── cross-source reconciliation
                 │   collect, LLM │      + grounded LLM hypothesis
                 │   judges       │
                 │  Layer C: LLM  │ ◀── trust profile composition
                 │   composes     │      (GREEN/YELLOW/RED + summary)
                 └────────┬───────┘
                          ▼ QualityReport (LayerA + LayerB + LayerC)
                 ┌────────────────┐
                 │ Human Review   │ ◀── interactive, structured panel
                 │ (4 categories, │      Approve | Reject(category, note)
                 │  retry-once)   │
                 └────────┬───────┘
                          ▼
                approved | reinvestigated | audit_required
```

**Two agents, three QA layers, one human gate.** The orchestrator is a LangGraph state machine (`workflow.py`); the agents are independent classes (`agents/sql_agent.py`, `agents/quality_agent.py`) that share no internal state — only Pydantic-typed messages.

The Quality Agent is internally three-layered:

- **Layer A — Rule-based fact collection.** High precision over recall: a check FAILs only when something is definitely wrong (null in a required field, zero where a positive value is expected, out-of-range value, etc.). Rules don't fire ≠ no problem.
- **Layer B — Cross-source reliability.** Rules execute the same metric against every source registered in `metrics.yml` and compute deltas. When sources disagree, an LLM proposes a *grounded* hypothesis — must cite specific findings or per-source notes, must declare what the hypothesis does NOT explain, MAY return null. The LLM never sees raw data, only structured findings.
- **Layer C — Trust profile composition.** An LLM takes Layer A + Layer B structured outputs and composes a `TrustProfile` for the reviewer: dimensions (correctness / source_reliability / ambiguity), overall verdict (GREEN/YELLOW/RED), one reviewer-facing summary sentence, and unresolved_questions populated when overall is RED. Falls back to a deterministic rule-based composer when no LLM is available.

The human review step is a real pause and decision point. The reviewer sees a Rich-formatted panel containing the trust profile, source provenance, the executed SQL with syntax highlighting, the Layer A check table, the Layer B findings table (every source / value / delta / notes — no truncation), the hypothesis (or absence note), and any `interpretation_choices`. On rejection, the reviewer picks one of four categories and writes a free-form note; the category drives routing (the note is appended to the agent's context but never parsed for routing). Rejections retry once per category; after that, items move to `audit_required` with an `AuditHandoff` package containing every attempted answer, every reject note, and Layer C's `unresolved_questions` for human follow-up.

### Decisions worth being explicit about

1. **The reviewer is the data team, not a business end user.** The panel shows SQL, source rationale, cross-source delta tables. This builds trust inside the data team first — the audience that can actually validate or contest the metric definitions.
2. **The Quality Agent generates a *trust profile*, not a pass/fail.** Reviewer cognitive load shifts from "hunt for problems" to "read a structured report." The system's output is the *credibility* of an answer along multiple dimensions, made explicit.
3. **The five questions are independent.** Q5's MoM trend does not reuse Q1's October GTV. Reproducibility is the precondition of the trust contract; chained dependencies make "the truth at this moment" unknowable.

---

## Context and Prompt Strategy

### Schema context (WrenAI-inspired, source-verified)

Before writing prompts I read [WrenAI](https://github.com/Canner/WrenAI)'s actual `schema_indexer.py` and `context.py` modules. The pattern that works at small scale: render each model as a markdown block with **full column descriptions** inline, including grain and per-table warning blocks. We adapted this in `metadata.describe_schema_context()` — every column from `_sources.yml` and `_models.yml` reaches the prompt with its dbt description, and `instructions.yml` carries per-table `⚠️` warnings (Total-row pollution, date-format gotchas, hidden status filters, canonical-source annotations).

Without this context, the LLM picked aggregated marts over fact tables, hid MTU ambiguity, and walked into the December stale-Total issue (see [Testing](#testing) for the diagnostic). With it, the LLM follows the registry and complies with warnings consistently.

### Metric registry (`metrics.yml`)

Inspired by WrenAI's MDL but scoped to one slice — metric definitions + cross-source reconciliation policy. Each entry declares:

- `primary` source (table, column, period_column, extra_filters, breakdown, aggregator)
- `alternatives` (for cross-source reconciliation by Layer B)
- `cross_source: required | optional | disabled`
- `period_start` / `period_end`
- `disagreement_threshold_pct`
- Optional `expected_min` / `expected_max` (Layer A plausibility)
- `notes_for_layer_b` (free-form context for the LLM hypothesis prompt)

The SQL Agent receives the registry entry as part of its user prompt — structured guidance instead of hand-coded rules. The Quality Agent's Layer B reads the same registry to drive cross-source SQL generation.

### Prompts live in files (V6 of the locked refactor decisions)

- `prompts/sql_agent_system.md` — process-driven, zero domain rule statements. The LLM is told *how* to reason from the registry + schema context, not *what* the answer should be.
- `prompts/sql_agent_user.md` — template with `{schema_context}` + `{registry_entry}` slots.
- `prompts/qa_layer_b_hypothesis.md` — bounded hypothesis prompt: must cite evidence, must declare what NOT explained, MAY return null.
- `prompts/qa_layer_c_compose.md` — trust profile composition prompt with verdict rubric and one few-shot.

### Domain knowledge lives in YAML, not Python

To onboard a new dataset (a different company, different dbt project), the changes required are:

1. Drop in their `_sources.yml` / `_models.yml`
2. Write a `metrics.yml` listing the questions and their canonical sources
3. (Optional) Write an `instructions.yml` with per-table warnings

No Python changes, no prompt changes. This is what "deliberate and extensible" means in our context — though see [Scaling](#scaling) for what breaks at hundreds of models.

### Alternatives considered

- **Pure raw schema dump** — tried first. The LLM can't tell `agg_monthly_biz_summary.gtv_idr` apart from `fct_trading_daily.gtv_idr` from column names alone. Schema descriptions disambiguate.
- **Hardcoded "use fct_trading_daily for GTV" rules in the system prompt** — worked but didn't generalize. Domain rules in YAML + a process-driven system prompt is the same outcome with a portable architecture.
- **Embedding retrieval over the schema** — overkill at 7 tables (full context is ~8KB). Documented in [Scaling](#scaling) as the next step for hundreds of models.

### Model Choice

Default: `openai/gpt-4.1-mini` on OpenRouter (configurable via `OPENROUTER_MODEL`).

Why: it's cheap (per-run cost ~$0.025 on the $5 evaluator key — see [Cost](#cost) below), fast, and reliable enough on structured-JSON-output tasks when the prompt has strict schema rules + a few-shot. We do not need a stronger model — the value-add comes from the metric registry + WrenAI-style schema context, not from raw model capability. The wrapper accepts any OpenAI-compatible endpoint via `OPENROUTER_BASE_URL`, so swapping providers is a config change.

---

## How to Run

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

### Configure `.env`

```bash
OPENROUTER_API_KEY=sk-or-...                                    # provided $5 key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openai/gpt-4.1-mini                            # cheap, capable
PLUANG_DATA_DIR=/path/to/pluang_analytics_agent/data            # provided CSVs
PLUANG_DB_PATH=var/pluang.sqlite
```

The wrapper detects the base URL: when it contains `openrouter`, requests include `extra_body={"usage": {"include": True}}` so per-call cost lands in `logs/cost.jsonl`.

### Load data into SQLite (idempotent)

```bash
pluang-agent setup
```

Running it twice does not duplicate data (the loader truncates and reloads each table per call).

### Run the pipeline end-to-end

```bash
# Interactive review (default — prompts you for approve/reject per question)
pluang-agent run

# Demo modes for sample-output generation
pluang-agent run --review-mode demo-approve
pluang-agent run --review-mode demo-reject     # rejects q5 with source_wrong, triggers reinvestigation
```

### Mock mode (no API key needed)

```bash
PLUANG_LLM_MOCK=1 pluang-agent run --review-mode demo-approve
```

Loads canned LLM responses from `tests/_fixtures/mock_llm/`. Useful for offline development; the test suite uses stub LLM clients directly so it runs without any fixtures.

### Check remaining OpenRouter credit

```bash
pluang-agent cost
```

---

## Sample Outputs

Committed in `outputs/sample/`, regenerated on the latest end-to-end run:

| File | What it shows |
|---|---|
| `sql_agent_answers.json` | All 5 SQL Agent answers with full provenance (`source.primary_table`, `source.why_chosen`, `sql`, `filters`, `assumptions`, `logic`, `interpretation_choices`, `dq_notes`, `usage` with token counts). |
| `quality_report.json` | All 5 Quality Agent reports — `layer_a` checks, `layer_b` cross-source findings + grounded hypothesis, `layer_c` trust profile + unresolved_questions. |
| `review_approval.log` | Demo-approve flow — every item approved, terminal `approved`. |
| `review_rejection_reinvestigation.log` | Demo-reject flow — q5 rejected with `source_wrong`, reinvestigated, reinvestigation diff (source.primary_table, trust profile, SQL first line) recorded inline. |

Historical deterministic-mode outputs from earlier development are preserved in `outputs/legacy_deterministic/` for comparison.

---

## Scaling

The current implementation works against 7 tables and 5 questions. Honest assessment of what breaks first when scaling to hundreds of dbt models:

1. **Full schema context per prompt — first thing to break.** `describe_schema_context()` is ~8KB at 7 tables. At 200 tables it would exceed token limits and degrade attention. **Fix:** add embedding retrieval over schema items (WrenAI's `schema_indexer.py` already implements this exact pattern — `extract_schema_items()` + LanceDB index). Retrieve the top-k tables relevant to the question keywords + metric registry entry. Switch from "always dump all" to "retrieve when threshold exceeded" (WrenAI uses 30K chars).

2. **Hand-curated `metrics.yml` — second thing to break.** A registry of 5 entries is maintainable; 500 isn't. **Fix:** source from dbt Semantic Layer / MetricFlow / WrenAI MDL, which already encode primary source, allowed dimensions, calculation expressions, and metric lineage. The current `MetricEntry` schema is intentionally a strict subset of these — migration would be mechanical.

3. **Question → metric matching is by `question_id` lookup, not semantic.** The registry is keyed by exact question id. Real users ask in English. **Fix:** add a routing step before the SQL Agent — embed the question, retrieve the top-k metric registry entries by description similarity, hand the candidates to the agent.

4. **Layer B runs SQL against every alternative source on every question.** At 5 alternatives × 5 questions = 25 extra queries per pipeline run, fine on SQLite. At hundreds of metrics × dozens of dimensions, cost balloons. **Fix:** materialize cross-source comparisons as scheduled dbt tests instead of agent-time, or use sampling for the cross-source check on high-cardinality breakdowns.

5. **One LLM call per layer per question.** 11 questions × 3 layers = 33 calls. **Fix:** batch — Layer A is rule-only, Layer B can batch hypotheses across questions in a single LLM call when sources / disagreement patterns repeat.

6. **`instructions.yml` warnings are hand-curated.** **Fix:** generate from dbt artifacts (test failures, freshness alerts, Elementary monitors) automatically. The format would stay the same — only the source of truth changes.

The architecture choice that makes all of this incremental: **domain knowledge lives in YAML files, agents read them at runtime, prompts are process-driven.** Adding retrieval, swapping the metric registry source, or batching LLM calls are all behind-the-interface changes — agents keep their current code, prompts keep their current text.

---

## Testing

### Test suite

```bash
pytest        # 46 tests, ~1.5s, no LLM required
ruff check .  # clean
```

Coverage:

- **`tests/test_contracts.py`** — every Pydantic schema round-trips through `model_validate`; the freeze contract holds.
- **`tests/test_data_loader.py`** — CSV → SQLite loader is idempotent; missing files raise `DataLoadError`.
- **`tests/test_sql_runner.py`** — SQL guard rejects DDL, DML, PRAGMA, and multi-statement queries.
- **`tests/test_system_error_escalation.py`** — `LLMAuthError` / `LLMQuotaError` / `LLMTransientError` / `LLMOutputError` all route to `AUDIT_REQUIRED(audit_reason='system_error')` with hand-off package populated. No silent fallback.
- **`tests/test_cost_log.py`** — `logs/cost.jsonl` writer schema verified.
- **`tests/test_layer_a_plausibility.py`** — `expected_min` / `expected_max` boundary conditions; non-metric column keys correctly skipped.
- **`tests/test_layer_b.py`** — generic Layer B reproduces every R0 finding on real data: Q1 ops disagrees ~13% (status filter), Q2 ops USD disagrees ~21% (fixed FX rate), Q3 NOT_APPLICABLE (definitional), Q4 ops disagrees, Q5 December stale (~3.5%).
- **`tests/test_layer_c.py`** — LLM stub returns valid JSON → parsed; LLM stub returns garbage → graceful fallback to rule-based composition. Both paths covered.
- **`tests/test_golden_sql.py`** — hand-written canonical SQL produces correct numbers on real CSVs. The "oracle" against which the LLM-mode answers are validated.

The test suite uses **stub LLM clients injected directly** — no fixtures need to be updated when prompts change. Mock mode (`PLUANG_LLM_MOCK=1`) is for interactive dev runs without an API key, not for tests.

### What did not work as expected (and how it was handled)

**The first real-LLM run was a silent disaster.** The LLM produced JSON that *looked* right on first inspection: source listed, SQL syntactically valid, fields all present. The numbers came back correct because the *deterministic baseline fallback* was running underneath. None of the LLM's actual SQL ever executed. Every committed sample output had `usage: null` and was, in fact, the deterministic answer in disguise.

The diagnostic was running the SQL Agent prompt manually against the LLM and dumping raw output (`scripts/inspect_llm_raw.py`). The model was returning:
- `filters: {"month": "2025-10"}` instead of `list[str]`
- `ambiguity_notes: ""` instead of `list[str]`
- `warnings: ""` instead of `list[str]`

Every answer was failing Pydantic validation; every fallback was hiding the failure. Two compounding fixes:

1. **The system prompt had to be explicit about types** ("filters is a list of strings — NEVER a dict") plus a few-shot example showing the exact JSON shape. With type-disciplined prompts most outputs validate; the rest go through one-shot repair.
2. **No silent fallback in the product path.** The deterministic baseline moved out of `src/` and into `tests/_fixtures/golden_sql.py` where it serves as a test oracle. Soft errors (bad output) become `SystemError(error_class='output')` and route to `AUDIT_REQUIRED` so the reviewer sees what happened.

The second discovery was semantic, not structural. After the JSON shape was tight, the LLM still consistently picked `agg_monthly_biz_summary` over `fct_trading_daily` for GTV — every monthly question, every time. Column names (`gtv_idr` in both tables) gave it no signal about which was canonical. The fix was reading [WrenAI's `schema_indexer.py`](https://github.com/Canner/WrenAI/blob/main/core/wren-base/src/wren-base/wren-py/wren/memory/schema_indexer.py) and copying their pattern: render the dbt YAML *descriptions* in the prompt, not just column names. With descriptions like *"GTV in IDR. SUM(amount_idr) for completed transactions only. Pending/failed/cancelled excluded"* in scope, the model picks the right source.

A third smaller find: Q4 transaction count returned `31` per asset class because the LLM wrote `COUNT(transaction_count)` instead of `SUM(transaction_count)` on a pre-aggregated daily mart (`fct_trading_daily` has 31 rows in October). Adding an explicit "this column is already an aggregate — use SUM" line in the prompt + an `instructions.yml` warning on the table fixed it.

These three bugs — schema discipline, semantic source selection, pre-aggregated mart confusion — drove most of the prompt engineering visible in the final system.

---

## Cost

### Observed end-to-end cost

Latest two pipeline runs (demo-approve + demo-reject) recorded in `logs/cost.jsonl`:

- **37 LLM calls** (across 2 runs, so ~18 per pipeline run on 5 questions)
- **86,826 prompt tokens + 9,093 completion tokens = 95,919 total** (across 2 runs)
- Per-run breakdown:
  - SQL Agent: ~6.5 calls × ~4,450 prompt + ~440 completion = ~31,800 tokens/run
  - Layer B hypothesis: ~5.5 calls × ~1,130 prompt + ~140 completion = ~7,000 tokens/run
  - Layer C composition: ~6.5 calls × ~1,275 prompt + ~140 completion = ~9,200 tokens/run

### Estimated cost on the $5 OpenRouter key

Using `openai/gpt-4.1-mini` pricing (as of submission: $0.40 / 1M input, $1.60 / 1M output via OpenRouter):

- **~$0.025 per pipeline run** (5 questions, full QA pipeline, both review modes ≈ 1 evaluation run)
- **~$0.05 for one full evaluation** (demo-approve + demo-reject end-to-end)
- The $5 key supports **~100 full evaluation runs** at this model — comfortably above any reasonable evaluator usage.

For development we used `gpt-4o-mini` against OpenAI native (~$0.009 per run). Switching to OpenRouter is a `.env` change.

### Deliberate token-saving decisions

- **Layer A is pure rules, no LLM.** Calling the LLM to count nulls and zeros would be cargo-culting; rules are 100% precision and cost zero.
- **Layer B's LLM only runs on disagreement.** When Layer B's verdict is `AGREE` or `NOT_APPLICABLE`, the hypothesis call is skipped and `hypothesis_absence_note` is populated by rule. ~30% of Layer B calls saved on a clean run.
- **Layer C's LLM falls back to rules on any failure.** Mock-mode-first design preserves end-to-end testability without LLM calls.
- **Schema context is rendered once per question, not per layer.** Layer B and Layer C only see structured findings (Decision 4 isolation), not the raw schema dump.
- **`logs/cost.jsonl` makes cost a first-class observable** — every real call is logged with `stage_tag`, `model`, `prompt_tokens`, `completion_tokens`, `cost_usd`. A reviewer can audit exactly what was billed.

---

## Limitations

The Quality Agent in production would still miss:

1. **Upstream freshness.** No check that `fct_trading_daily` was actually built today. Elementary's freshness/volume monitors would slot in here as a Layer A rule reading dbt artifacts.
2. **Implausible-but-locally-valid values.** A 100× spike in GTV that doesn't exceed `expected_max` would pass Layer A. Statistical anomaly detection (z-score, IQR, seasonal decomposition) requires historical baselines this prototype does not have.
3. **Joins and grain mismatches across many tables.** Layer B compares pre-aggregated metric values per source; it does not detect that a join in the LLM's SQL silently created Cartesian products. SQL EXPLAIN-based detection would be the next step.
4. **Definitional drift over time.** If `fct_trading_daily.gtv_idr` quietly changes its `WHERE status = 'completed'` filter to `WHERE status != 'failed'`, all our cross-source agreement assumptions break and the system would not notice. dbt model contracts + Elementary schema-change alerts are the production answer.
5. **External truth.** The reviewer's `external_disagreement` category exists for "this disagrees with the dashboard I just looked at," and the system has no way to verify external sources. By design — that's the human's job.
6. **Audit closure.** `AUDIT_REQUIRED` ships a hand-off package with `unresolved_questions`, but there's no in-system way for the human to mark an audit "closed with the following resolution." Closing the loop would require a persistence layer this prototype does not have.
7. **Multi-language support.** All prompts and column descriptions are English. Pluang's actual context may include Bahasa Indonesia metric definitions; the schema-context renderer would need translation before this would generalize there.
8. **Reviewer note quality.** The HITL panel coaches the reviewer with category-specific note starters, but a vague free-form note still propagates into the reinvestigation prompt and may not change LLM behavior. Better: a structured note schema (point to specific finding, propose specific alternative).

With more time I would prioritize, in order:

1. **dbt Semantic Layer / MetricFlow integration** — replace the hand-curated `metrics.yml` with a real semantic layer adapter. This is the load-bearing assumption for the scaling story.
2. **Embedding-based retrieval over schema and past NL→SQL pairs** — the WrenAI-style memory pattern. Currently both are static.
3. **Layer A statistical checks** — z-score against historical baselines stored alongside the canonical mart. Would catch the "implausible-but-locally-valid" gap above.
4. **Reinvestigation diff in the LLM's reinvestigation prompt** — when the reviewer rejects with `source_wrong`, the agent should see "you used X, the canonical primary is Y, the registry says Y is preferred for this metric." Currently the reviewer note is the only carrier.

---

## Project structure

```
src/pluang_agent/
  agents/
    sql_agent.py           SQL Agent — LLM-driven, no fallback
    quality_agent.py       3-layer A/B/C orchestrator (no shared state with SQL Agent)
  layer_b.py               Generic cross-source reconciliation + LLM hypothesis
  layer_c.py               LLM-driven trust profile composition (rule-based fallback)
  metadata.py              dbt YAML loader + WrenAI-style describe_schema_context()
  metrics.py               metrics.yml registry loader (MetricEntry, SourceSpec)
  models.py                Pydantic contracts (frozen at R1)
  llm.py                   OpenRouter client + typed errors + cost log
  workflow.py              LangGraph state machine (4-category retry, audit hand-off)
  review.py                HITL — Rich panel, Layer B table, hypothesis, note coaching
  cli.py                   Typer entrypoint
  data_loader.py           Idempotent CSV → SQLite loader
  db.py                    Sqlite connect + schema introspection
  sql_runner.py            Read-only SQL guard
  questions.py             The 5 required business questions

prompts/
  sql_agent_system.md      Process-driven (no Pluang-specific rules)
  sql_agent_user.md        Template with {schema_context} + {registry_entry} slots
  qa_layer_b_hypothesis.md Bounded hypothesis prompt
  qa_layer_c_compose.md    Trust profile composition prompt

metrics.yml                Per-question metric registry (primary, alternatives, bounds)
instructions.yml           Per-table warnings (Total-row, date format, status filters)

tests/                     46 tests, all run without an API key
outputs/sample/            Committed sample outputs from latest run
outputs/legacy_deterministic/  Historical pre-refactor outputs for reference
```

### Influence credits

- **WrenAI** ([github.com/Canner/WrenAI](https://github.com/Canner/WrenAI)) — read their `schema_indexer.py` and `context.py` directly. Adopted the `### Model: name — description` schema rendering pattern and the per-table warnings injection. Did NOT adopt: the Apache DataFusion engine, MDL → modeled SQL, semantic search retrieval, governed access patterns. Documented in [Scaling](#scaling) as the next step.
- **Elementary** ([github.com/elementary-data/elementary](https://github.com/elementary-data/elementary)) — adopted the rule-based-anomaly-detection-as-tests discipline for Layer A. Did NOT adopt: dbt artifact ingestion, freshness/volume/schema-drift monitors. Documented as Limitation #1.

The 3-layer A/B/C structure with strict isolation (C never sees raw data), the trust-profile-as-output framing, the audit hand-off package, and the reject-via-category-no-NL-parsing pattern are original to this design.

### Submission notes

- 46 tests pass, `ruff check .` clean.
- Sample outputs in `outputs/sample/` regenerated from a real LLM run (every `usage` populated).
- Cost log committed at `logs/cost.jsonl` with per-call breakdown.
- `data/` and `.env` are gitignored; no API key in the repo.
