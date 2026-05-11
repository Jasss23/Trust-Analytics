# Pluang Multi-Agent Analytics Reporting System

Take-home submission for Pluang's Machine Learning Engineer (Agents & Data Intelligence) case study.

The system answers business questions over the provided SQLite-loaded CSV data, validates each answer before review, and pauses for a human approval / rejection decision. It is intentionally a working prototype, not a production platform. The main engineering principle is: **return a source-grounded, shape-complete answer, or route to audit instead of pretending success.**

---

## Architecture

```text
question
  -> Question Planner (skeleton)
  -> Planner QA Gate (deterministic validator)
  -> Derivation Trace (LLM proposes; deterministic validator gates)
  -> SQL Agent (writes SQL conforming to plan + trace)
  -> read-only SQL execution
  -> semantic pre-flight / answer-shape validation
  -> Quality Agent
       Layer A: deterministic data + shape checks
       Layer B: cross-source reconciliation + grounded hypothesis
       Layer C: reviewer-facing trust profile
  -> Human Review
       approve | reject(category + note) | audit_required
          \-> on answer_wrong/source_wrong: LLM-revise plan,
              re-derive trace, re-run SQL Agent (R8)
```

### Components

| Component | Role |
|---|---|
| `planner.plan_question` | Builds a typed `QuestionPlan` (skeleton): metric intent, period, source policy, answer shape, required columns, ambiguity policy, validation rules. |
| `planner.replan_question` | R8 — LLM-revises an existing plan with reviewer feedback on reinvestigation; validator gates the revision. Generic over any plan-changing reject note (wrong metric, wrong aggregator, wrong source, wrong shape). |
| Planner QA | Deterministic validator: table/column grounding, period bounds, breakdown intent, MoM intent, multi-definition intent, Total-row exclusion, source policy. Runs on initial AND revised plans identically. |
| Derivation Trace | LLM proposes a structured `DerivationTrace` (required grain, scope predicates, candidate sources with grain_match + scope_feasibility, chosen source/filters/aggregator). Deterministic validator checks structural invariants. The reviewer-facing `why_chosen` is code-rendered from this trace, not LLM-narrative. |
| SQL Agent | LLM-driven SQL proposal from the validated plan + trace + schema context + metric registry. Single LLM call per invocation; retry policy lives in the state machine, not the agent. Does not author `why_chosen` (planner-owned). |
| SQL runner | Executes only read-only single-statement `SELECT` / `WITH` SQL. Rejects DDL, DML, PRAGMA, and multi-statement SQL. |
| Semantic pre-flight | Checks executed rows against the plan AND trace: required columns, source match, no aggregate members in breakdowns, multi-definition completeness, MoM percent sanity, filter coverage, ambiguity surfacing on multi-feasible-candidate traces. |
| Quality Agent | Runs Layer A/B/C and produces a `QualityReport` for every answer. |
| Human Review | Rich terminal review panel with Key Facts header + Derivation panel + pretty SQL. Rejections require category + note; on `answer_wrong`/`source_wrong` the planner LLM-revises the plan before the SQL Agent retries. |

The Planner is deliberately not trusted. Its output must pass deterministic validation before the SQL Agent is called. This was added after review found that prompt-only instructions could still produce incomplete answers, e.g. MTU ambiguity text without all MTU values, or a MoM trend without MoM change fields.

The Derivation Trace was added because the SQL Agent's free-form `why_chosen` field tended to be LLM-narrative boilerplate even when the schema technically passed validation. Making the planner own `why_chosen` via a structured trace + code-rendered string means boilerplate is structurally impossible — the reviewer sees an auditable derivation (candidates considered, grain match per candidate, scope feasibility per predicate, chosen source with rendered process trace) rather than a confident sentence.

R8's plan revision on reinvestigation closes the reviewer-feedback loop: when the reviewer correctly identifies a metric / aggregator / source / shape error, the LLM proposes a revised plan and the deterministic validator gates it. Before R8, the reviewer note flowed into the SQL Agent's correction context but not into the planner — so the SQL Agent would generate a correct new answer that pre-flight then rejected for plan-vs-answer mismatch.

### Agent Separation

The SQL Agent and Quality Agent remain independently testable and do not share internal state. The bridge between them is typed data:

- `QuestionPlan`
- `SQLAgentAnswer`
- `QualityReport`
- `ReviewDecision`
- `AuditHandoff`

The Quality Agent receives the SQL Agent's structured answer and the validated plan; it does not call back into the SQL Agent.

---

## Context and Prompt Strategy

### Schema Context

`metadata.describe_schema_context()` renders the dbt YAML as WrenAI-style blocks with full table and column descriptions:

```text
#### Model: fct_trading_daily — ...
  ⚠️ Canonical completed-transaction source...
  Columns:
    - transaction_date: ...
    - gtv_idr: ...
```

This matters because raw column names alone are not enough to choose between `fct_trading_daily.gtv_idr`, `agg_monthly_biz_summary.gtv_idr`, and `mart_ops_dashboard.gtv_idr`.

### Metric Registry

`metrics.yml` carries metric semantics:

- canonical primary source
- alternative sources
- period bounds
- aggregation grain
- breakdown dimension
- cross-source threshold
- plausibility bounds
- Layer B notes

The registry is not a hardcoded answer table. It defines source policy and metric semantics so the agents can generate SQL and QA can reconcile sources.

### Planner Contract

The Planner produces an answer-shape contract before SQL:

- `scalar`
- `breakdown`
- `multi_definition`
- `time_series`
- `period_over_period`
- `breakdown_comparison`

Examples:

- MTU becomes `multi_definition` and must return `aum_defined_mtu`, `raw_completed_unique_traders`, and `mixpanel_mtu`.
- “Month-on-month trend” becomes `period_over_period` and must return `month`, `gtv_idr`, `mom_change_idr`, and `mom_change_pct`.
- “Ops dashboard by asset class compared to canonical” becomes `breakdown_comparison` and must exclude `asset_class = 'Total'`.

### Prompts

Prompts live in `prompts/`:

- `sql_agent_system.md` — SQL Agent role + process + output contract. `why_chosen` is explicitly planner-owned (do not author it).
- `sql_agent_user.md` — per-question template with slots for schema context, registry entry, validated plan, derivation trace, reviewer note, correction block.
- `planner_trace.md` — LLM proposes a structured `DerivationTrace` from schema + registry + skeleton plan. Validator gates structurally.
- `planner_revise.md` (R8) — LLM revises an existing plan on `answer_wrong` / `source_wrong` rejection. Hard rules: only change fields the reviewer note implies, preserve `question_id`, real tables/columns only.
- `qa_layer_b_hypothesis.md` — bounded grounded hypothesis on cross-source disagreement.
- `qa_layer_c_compose.md` — trust profile composition from structured Layer A + B output.

The prompts explain process and output contracts. The hard guarantees come from typed validation and executed SQL, not from trusting prompt compliance.

---

## Model Choice

Final default:

```env
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

I evaluated two OpenRouter models with the provided key:

| Model | Calls Evaluated | Total Tokens | Cost | Result |
|---|---:|---:|---:|---|
| `openai/gpt-4.1-mini` | 29 | 75,568 | `$0.0276304` | Passed Q1-Q5 approve; demo-reject routed bad Q5 reinvestigation to audit. |
| `openai/gpt-4o-mini` | 59 | 161,596 | `$0.0233496` | Passed Q1-Q5 approve; demo-reject was safely handled by reinvestigation/audit gates; extra Ops probe passed. |

I chose `openai/gpt-4o-mini` because it passed the hard gates, was cheaper in observed OpenRouter usage, and handled rejection paths without silently accepting bad source changes. The architecture does not depend on this model: the model is configurable through `OPENROUTER_MODEL`.

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
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PLUANG_DATA_DIR=/path/to/pluang_analytics_agent/data
PLUANG_DB_PATH=var/pluang.sqlite
```

Do not commit `.env`.

### Load Data

```bash
pluang-agent setup
```

The loader is idempotent: it drops/recreates the target SQLite tables, so rerunning setup does not duplicate rows.

### Run End To End

```bash
# Interactive human review
pluang-agent run

# Deterministic review demos for evidence generation
pluang-agent run --review-mode demo-approve
pluang-agent run --review-mode demo-reject
```

### Ask Your Own Question

The `ask` command takes any natural-language question and runs it through the same pipeline as the five demo questions — hybrid planner (LLM-proposed `DerivationTrace` + deterministic validator) → SQL Agent → execute → pre-flight → QA → human review.

```bash
# Free-form question; metric + period inferred from the text
pluang-agent ask "What was total GTV (USD) in October 2025?"

# Skip the interactive review prompt (CI / scripted use)
pluang-agent ask "How many crypto transactions in November 2025?" --no-review

# Override the inferred metric or period when the heuristics get it wrong
pluang-agent ask "Year-end snapshot" --metric gtv_idr --period "December 2025"
```

Outputs are written to `outputs/ask/<synthesised_id>/` (a fresh subdirectory per invocation so the five-question demo samples stay clean). Each ad-hoc run produces the same four files as `run`: `sql_agent_answers.json`, `quality_report.json`, `question_plans.json`, and a `review_*.log`.

When the planner can't construct a defensible derivation trace (unregistered metric the LLM can't ground in the schema, ambiguous question with no canonical source, etc.), the question routes to `audit_required` with a structured `AuditHandoff`. The CLI prints the failure reason and exits non-zero — *no question crashes the pipeline*, but not every question gets answered.

When the reviewer rejects an ad-hoc answer with `answer_wrong` or `source_wrong`, the planner LLM-revises the plan with the reviewer note (R8) before the SQL Agent retries. So a note like *"I want row count, not sum of value"* on a heuristically-misinferred question now actually flips the plan to `aggregator=COUNT_DISTINCT` and reaches `reinvestigated` instead of bouncing to `audit_required` on plan-vs-answer mismatch.

### Check OpenRouter Credit

```bash
pluang-agent cost
```

### Mock Mode

```bash
PLUANG_LLM_MOCK=1 pluang-agent run --review-mode demo-approve
```

Unit tests do not require an API key.

---

## Final Verified Run

The final terminal transcript is committed at:

```text
outputs/sample/final_run_terminal.log
```

Final verification summary:

```text
ruff check .: clean
pytest: 71 passed, 1 warning
demo-reject: 4 approved, 1 audit_required
demo-approve: 5 approved
```

Final sample output sanity:

- Q3 returns all three MTU definitions:
  - `aum_defined_mtu = 12453`
  - `raw_completed_unique_traders = 9239`
  - `mixpanel_mtu = 8882`
- Q5 returns MoM trend fields:
  - November `mom_change_idr = 564303708`, `mom_change_pct = 1.4795673267069418`
  - December `mom_change_idr = -99364678`, `mom_change_pct = -0.2567291901663355`
- Demo reject routes a bad Q5 reinvestigation to `audit_required` when the model tries to switch to the stale `agg_monthly_biz_summary` source.

Final two-run verification cost, from `logs/cost.jsonl` starting at `2026-05-11T07:07:52Z`:

```json
{
  "calls": 28,
  "prompt_tokens": 73362,
  "completion_tokens": 7323,
  "total_tokens": 80685,
  "cost_usd": 0.0107421
}
```

---

## Sample Outputs

Committed in `outputs/sample/`:

| File | Purpose |
|---|---|
| `question_plans.json` | Validated Planner output for all five required questions. |
| `sql_agent_answers.json` | SQL Agent answers from the final demo-approve run, including source provenance, SQL, result rows, interpretation choices, and usage. |
| `quality_report.json` | Quality Agent reports for all five answers: Layer A checks, Layer B reconciliation, Layer C trust profile. |
| `review_approval.log` | Demo-approve review evidence. |
| `review_rejection_reinvestigation.log` | Demo-reject evidence showing Q5 rejection and audit handoff when reinvestigation violates source policy. |
| `final_run_terminal.log` | Terminal transcript for final ruff, pytest, demo-reject, and demo-approve run. |

Historical pre-refactor outputs remain under `outputs/legacy_deterministic/` for development comparison only.

---

## Testing

```bash
pytest
ruff check .
```

Current test status:

```text
160 passed
ruff clean
```

Bi-directional test discipline (hard rule from R6): every plan-driven check ships with a trigger test that fails AND an inverse test that passes the opposite scenario. Example: `aggregate_member_in_breakdown` has a triggering test (plan excludes `Total`, answer includes a `Total` row → fail) AND an inverse test (plan does NOT exclude `Total`, answer includes a `Total` row → pass). This prevents the "coded to Q1–Q5" failure mode — every mechanism must be plan-driven or data-driven, never question-id-driven.

Coverage highlights:

- `test_sql_runner.py`: read-only SQL guard rejects mutation, PRAGMA, and multi-statement SQL.
- `test_data_loader.py`: CSV loading is idempotent.
- `test_contracts.py`: Pydantic contracts round-trip.
- `test_planner.py`: Planner QA enforces MTU multi-definition, MoM trend fields, Ops/canonical comparison, and Total-row exclusion.
- `test_planner_trace.py`: Derivation trace validator rejects hallucinated grains, missing rejection reasons, dangling chosen-source references, infeasible scope predicates, hallucinated filter columns, aggregator rationale without grain-mention.
- `test_pre_flight.py`: bi-directional coverage of every plan-driven and trace-driven shape check (32 tests).
- `test_sql_attempt.py`: success path, exec failure → schema-hint correction → success, pre-flight failure → retry → success, budget exhaustion → SystemError.
- `test_replan_question.py` (R8): reviewer note motivates plan change → revision validates; SQL-only complaint → plan unchanged via `revision_note`; hallucinated revision → validator rejects.
- `test_workflow_reinvestigation.py` (R8): end-to-end replay of "row count, not SUM" reviewer-correction case → terminal=reinvestigated, not audit_required.
- `test_cli_ask.py`: `pluang-agent ask` happy path, overrides honoured, planner failure exits non-zero.
- `test_synthesize_question.py`: metric/period inference, slug+timestamp id determinism, override precedence.
- `test_layer_b.py`: cross-source reconciliation catches Ops status-filter deltas, fixed-FX USD deltas, and stale December Total row.
- `test_layer_c.py`: LLM Layer C parses valid JSON and falls back safely on invalid output.
- `test_system_error_escalation.py`: auth/quota/transient/output failures route to audit rather than deterministic fallback.
- `test_review.py`: HITL panel formatter unit tests (Key Facts, Derivation panel, pretty SQL).
- `test_golden_sql.py`: fixture-only canonical SQL verifies numeric truth on the real CSVs.

---

## Cost

Every live LLM call appends one JSON line to `logs/cost.jsonl`:

```json
{
  "ts": "...",
  "stage_tag": "sql_agent:q5_gtv_mom_trend_oct_dec_2025",
  "model": "openai/gpt-4o-mini",
  "prompt_tokens": 4377,
  "completion_tokens": 511,
  "total_tokens": 4888,
  "cost_usd": 0.00063675
}
```

Cost-saving choices:

- Planner QA and semantic pre-flight are deterministic.
- Layer A is deterministic.
- Layer B calls the LLM only when it needs a hypothesis for disagreement.
- Layer C falls back to deterministic composition if the LLM fails.
- The SQL Agent never performs deterministic answer fallback; failed output routes to retry or audit.

Observed final verification cost was about `$0.0107` for demo-reject + demo-approve. This is well within the provided `$5` OpenRouter key.

---

## Scaling

Scaling is a routing problem: the architecture that handles the prototype's seven tables does not need to be the same architecture that handles a thousand. Below is the threshold router we'd build, with concrete triggers, what breaks first, and the code changes that come online at each tier.

### Tier 1 — current prototype scale (≤ 50 dbt models, ≤ 100 hand-curated metrics, single tenant)

Architecture as shipped. Full schema dump into the prompt; hand-curated `metrics.yml`; planner = registry lookup + heuristic fallback + LLM trace proposal + deterministic validator (Hybrid).

| Bottleneck | First failure mode | Code path |
|---|---|---|
| Schema context size | Prompt exceeds context window at ~50 tables (~30K tokens of dbt YAML) | `metadata.describe_schema_context` |
| Metric registry curation | 1–2h human effort per metric | `metrics.yml`, `instructions.yml` |
| Per-question cost | ~$0.005 with the trace LLM call | `logs/cost.jsonl` |

### Tier 2 — growing dbt project (50–500 models, 100–1000 metrics, still single tenant)

What breaks first: schema context, planner coverage on unregistered metrics. What we'd ship:

1. **Schema retrieval.** Embed dbt YAML descriptions (model + column + warning). Top-K relevant tables get injected into the prompt instead of all of them. The trace validator already cross-checks against full metadata, so retrieval is a prompt-construction optimisation; the gate is untouched. — Touches `metadata.py`, adds `tests/test_schema_retrieval.py`.
2. **LLM planner candidate generator at scale.** The trace LLM call already proposes a `DerivationTrace`; for unregistered metrics, the same call also drafts the skeleton `QuestionPlan`. Validator stays deterministic. — Touches `planner.py`.
3. **Generic Layer B fallback.** When a metric is not in `metrics.yml`, layer_b infers candidate alternatives from `describe_schema_context()` instead of falling back to `NOT_APPLICABLE`. Coverage stays correlated with schema, not registry. — Touches `layer_b.py`.

| Bottleneck | First failure mode | Code path |
|---|---|---|
| Multi-tenant credentials | Single `OPENROUTER_API_KEY` env var; one SQLite path | `cli.py`, `sql_runner.py` |
| Cost attribution | `cost_usd` null for non-OpenRouter providers | `llm.py` |
| Fixture staleness | Prompt drift produces stale fixtures silently | `tests/_fixtures/mock_llm/` |

### Tier 3 — production scale (> 500 models or multi-tenant)

What breaks first: human curation cost, audit-as-terminal, single-tenant assumptions. What we'd ship:

1. **dbt Semantic Layer adapter.** Read MetricFlow MDL directly. `metrics.yml` becomes an export, not a source of truth. Warnings sync from dbt test artifacts and freshness checks. — Replaces `metrics.py` / `instructions.yml`.
2. **Audit → registry feedback loop.** `AuditHandoff` packages today are a dead-end. At scale, aggregate rejections by category over time, surface a weekly "registry improvements" report (e.g. "every Q5-style rejection pointed at the December stale Total → tighten `expected_max`"). Closes the learning loop the prototype lacks. — New `audit_miner.py` + scheduled job.
3. **Per-tenant connection pool + cost attribution + dataset access isolation.** SQLAgent gets `tenant_id`; warehouse adapter routes credentials and tracks spend per tenant per question. — Touches `cli.py`, `sql_runner.py`, `llm.py`.

| Bottleneck | First failure mode | Code path |
|---|---|---|
| Warehouse coupling | Hard-coded SQLite execute | `sql_runner.execute_read_only` |
| Audit closure | Manual triage only | `workflow._build_audit_handoff` |
| Multi-language metric definitions | English-only schema/registry | `metadata.py`, `metrics.yml` |

### Ambitious vision

The three Tier 2/3 improvements compose into one architectural trajectory:

- **The Planner stays the gatekeeper.** Today's hybrid (LLM proposes trace → deterministic validator → render) is the same shape we'd run at 10K models. Validators get more rules; the gate doesn't move.
- **The metric registry stops being hand-curated.** Tier 3's dbt Semantic Layer adapter feeds the planner from MDL. The five-question demo's `metrics.yml` was illustrative; the real source of truth is upstream.
- **Audit is a signal, not a bin.** Tier 3's audit-feedback loop turns rejections into registry-tightening suggestions. The prototype's `AuditHandoff.unresolved_questions` is one row of an eventual dataset that the planner is trained against.

The architectural decision that buys us this trajectory is that the **Planner QA Gate, SQL execution, Pre-flight, Quality Agent, and Human Review are independently testable, independently swappable layers**. Retrieval, semantic-layer backends, and audit miners can replace internals without removing or weakening validation gates.

---

## Limitations

- The planner is deterministic and deliberately conservative. Unknown question shapes may route to audit rather than forcing an answer.
- Freshness is not checked against dbt artifacts or warehouse build metadata.
- Plausibility bounds are hand-curated and do not include statistical anomaly detection.
- Layer B reasons over registered or planned sources; it does not verify external dashboards.
- Human review decisions are captured in logs, not persisted in a database.
- Multi-language metric definitions are not supported.
- Reviewer note quality still matters; the system validates reinvestigation output, but vague notes may not help the model repair its SQL.

---

## Project Structure

```text
src/pluang_agent/
  planner.py              skeleton plan + LLM trace + LLM revise (R8) + validator
  agents/sql_agent.py     LLM SQL proposal from validated plan + trace
  agents/quality_agent.py Layer A/B/C orchestrator
  sql_attempt.py          one-attempt orchestration (agent + execute + pre-flight)
  pre_flight.py           plan-driven + trace-driven shape validation
  layer_b.py              cross-source reconciliation
  layer_c.py              trust profile composer
  workflow.py             LangGraph orchestration + unified retry + HITL routing
  review.py               Rich review panel (Key Facts header + Derivation panel)
  llm.py                  OpenRouter client + cost logging + mock client
  sql_runner.py           read-only SQL execution guard
  metadata.py             dbt YAML schema context renderer
  metrics.py              metrics.yml loader
  questions.py            REQUIRED_QUESTIONS + ad-hoc question synthesis
  cli.py                  setup / run / ask / cost / review-demo commands
  models.py               Pydantic contracts (QuestionPlan, DerivationTrace, etc.)

prompts/
  sql_agent_system.md     SQL Agent system prompt
  sql_agent_user.md       per-question user template
  planner_trace.md        derivation trace proposal prompt
  planner_revise.md       plan revision prompt (R8)
  qa_layer_b_hypothesis.md
  qa_layer_c_compose.md

metrics.yml               metric registry
instructions.yml          per-table ⚠️ warnings
outputs/sample/           final submission evidence (five demo questions)
outputs/ask/              gitignored — per-invocation ad-hoc question artifacts
tests/                    160 unit + integration tests
```

---

## Submission Notes

- No API key is committed.
- Provided CSV data is not committed.
- SQLite databases and local runtime files are ignored.
- `logs/cost.jsonl` is committed as evaluator-visible cost evidence.