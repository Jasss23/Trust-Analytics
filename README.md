# Pluang Multi-Agent Analytics Reporting System

> Take-home submission for Pluang's Machine Learning Engineer (Agents & Data Intelligence) case study. A **SQL Agent → Quality Agent → Human Review** pipeline that returns a *source-grounded, shape-complete answer* — or routes to audit instead of pretending success.

---

## TL;DR

### What runs

```text
question
  → Question Planner (skeleton plan + LLM-proposed DerivationTrace + deterministic validator)
  → SQL Agent (writes SQL conforming to plan + trace)
  → read-only SQL execution + semantic pre-flight
  → Quality Agent
        Layer A — FLAGS (deterministic: nulls / zeros / negatives / range / shape)
        Layer B — FLAGS + HYPOTHESIS (cross-source reconciliation; LLM grounded hypothesis or null)
        Layer C — trust profile (correctness / source / ambiguity) + reviewer summary
  → Human Review (Rich panel; rejection requires category + note)
        approve | reject(category, note) | audit_required
            \→ on answer_wrong / source_wrong: planner LLM-revises plan, re-derive trace, re-run SQL Agent
```

### Result on the 5 required questions

| Demo | Q1 GTV (IDR)/asset | Q2 GTV (USD) | Q3 MTU | Q4 Tx count/asset | Q5 GTV MoM trend |
|---|---|---|---|---|---|
| `demo-approve` | approved (RED) | approved (RED) | approved (YELLOW) | approved (RED) | approved (RED) |
| `demo-reject` | approved (RED) | approved (RED) | approved (YELLOW) | approved (RED) | **rejected → audit_required (RED)** |

Trust = RED on most questions because Layer B cross-source reconciliation **legitimately disagrees** (mart_ops_dashboard includes pending transactions; agg_monthly_biz_summary has a stale December Total row). The reviewer sees the disagreements and the grounded hypothesis explaining each. RED is the right answer; "all green" would be the bug.

The Q5 rejection on `demo-reject` flows through the R8 plan-revision path — the LLM tries to switch to the stale `agg_monthly_biz_summary` source, pre-flight catches it, and the item terminates at `audit_required` (the deliberate landing for "we tried and it still doesn't reconcile"). A separate successful reinvestigation log (`outputs/sample/review_reinvestigation_success.log`) shows R8's happy path — reviewer says *"row count, not sum"*, planner LLM-revises `SUM → COUNT_DISTINCT`, pre-flight passes, terminal = `reinvestigated`.

### Reproduce in 3 commands

```bash
bash setup.sh             # venv + editable install + .env from template
pluang-agent setup        # idempotent CSV → SQLite load
pluang-agent run --review-mode demo-reject   # also: --review-mode demo-approve
```

### Project stats

- **160 tests pass**, ruff clean.
- **Final end-to-end audit run**: 22 LLM calls, 75,166 tokens, **$0.0125** of the $5 OpenRouter key budget (= 0.25%).
- **Cumulative dev cost** (every call ever logged): 295 calls, 803,104 tokens, **$0.0635** (= 1.27%).

---

## Reading Guide (for the 30-minute walkthrough)

Suggested order — each item adds context that motivates the next:

1. **This README**, sections in order — the system, the trade-offs, the evidence.
2. **`AGENTS.md`** — invariants and non-negotiables (no silent fallback, agent independence, read-only SQL).
3. **`outputs/sample/`** — committed evidence (no need to run anything):
   - `sql_agent_answers.json` — five structured SQL Agent outputs with full source provenance + derivation trace.
   - `quality_report.json` — five Layer A/B/C reports with flags + hypotheses.
   - `review_approval.log` — demo-approve, 5 approved.
   - `review_rejection_reinvestigation.log` — demo-reject, Q5 rejected → audit_required via gated reinvestigation.
   - `review_reinvestigation_success.log` — R8's happy path: rejection → plan revision → approved.
   - `review_panel_rendered.txt` — the Rich panel a human reviewer actually sees (Q1).
   - `final_run_terminal.log` — terminal transcript (ruff + pytest + the two demo runs).
4. **Code by orchestration depth** (only as deep as you want to go):
   - `src/pluang_agent/workflow.py` — LangGraph state machine; routing on reject category.
   - `src/pluang_agent/planner.py` — skeleton plan + LLM trace + R8 plan revision.
   - `src/pluang_agent/agents/sql_agent.py` — SQL proposal.
   - `src/pluang_agent/layer_b.py` — cross-source reconciliation + grounded hypothesis.
   - `src/pluang_agent/review.py` — HITL panel rendering.
5. **Tests that pin the R8 story**: `tests/test_workflow_reinvestigation.py` + `tests/test_replan_question.py`.

---

## Evaluation Criteria Coverage

Direct map from the case study's rubric to where the evidence lives:

| Dimension | Where it lives | Pointer |
|---|---|---|
| **SQL Agent accuracy** | All five questions answered with full provenance (`source`, `filters`, `assumptions`, `logic`, `sql`, `derivation_trace`, `result_rows`). Golden-SQL tests pin numeric truth against the real CSVs. | `outputs/sample/sql_agent_answers.json`; `tests/test_golden_sql.py` |
| **Context and prompt engineering** | Schema context is rendered WrenAI-style from dbt YAML (full descriptions + `⚠️` warnings). Metric semantics live in `metrics.yml`, per-table warnings in `instructions.yml`, prompts contain process rules only — zero hardcoded domain rules. | §[Context and Prompt Strategy](#context-and-prompt-strategy); `prompts/`; `metrics.yml`; `instructions.yml` |
| **Quality Agent reasoning** | Three-layer A/B/C with the case study's flag-first / hypothesise-where-grounded split surfaced explicitly in the panel UI. Hypotheses cite evidence, declare confidence, declare what they do NOT explain, and MAY be null. | §[Quality Agent — Flag vs Hypothesis](#quality-agent--flag-vs-hypothesis); `outputs/sample/quality_report.json`; `outputs/sample/review_panel_rendered.txt` |
| **Human review design** | Rich-formatted panel with Key Facts + Derivation candidates + structured key:value why_chosen + syntax-highlighted SQL + QA Summary (FLAGS vs HYPOTHESIS) + Layer A/B/Hypothesis blocks. Rejections require category + note. Category drives routing; note travels as context. | `outputs/sample/review_panel_rendered.txt`; `outputs/sample/review_rejection_reinvestigation.log`; `outputs/sample/review_reinvestigation_success.log`; `src/pluang_agent/review.py` |
| **Architecture** | Deterministic validators gate every LLM step. Agents communicate via frozen Pydantic contracts only — no shared internal state. Retry budget unified on `PipelineItem`, not in agents. | §[Architecture](#architecture); `src/pluang_agent/workflow.py` |
| **Self-evaluation** | Limitations framed as deliberate trade-offs with the future-value step explicit. Includes the things this prototype *didn't measure* — most importantly, no scaled evaluation harness yet. | §[Limitations — and what I'd ship first with another week](#limitations--and-what-id-ship-first-with-another-week) |

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
       Layer A: deterministic FLAGS (data + shape)
       Layer B: cross-source FLAGS + grounded HYPOTHESIS (LLM, may be null)
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
| SQL Agent | LLM-driven SQL proposal from the validated plan + trace + schema context + metric registry. Single LLM call per invocation; retry policy lives in the state machine. Does not author `why_chosen` (planner-owned). |
| SQL runner | Executes only read-only single-statement `SELECT` / `WITH` SQL. Rejects DDL, DML, PRAGMA, and multi-statement SQL. |
| Semantic pre-flight | Checks executed rows against the plan AND trace: required columns, source match, no aggregate members in breakdowns, multi-definition completeness, MoM percent sanity, filter coverage, ambiguity surfacing on multi-feasible-candidate traces. |
| Quality Agent | Runs Layer A/B/C and produces a `QualityReport` for every answer. |
| Human Review | Rich terminal review panel: Key Facts + Derivation candidates table + structured key:value why_chosen + syntax-highlighted SQL + **QA Summary (FLAGS vs HYPOTHESIS)** + Layer A/B blocks + hypothesis panel. Rejections require category + note; on `answer_wrong`/`source_wrong` the planner LLM-revises the plan before the SQL Agent retries. |

The Planner is deliberately not trusted. Its output must pass deterministic validation before the SQL Agent is called. The Derivation Trace was added because the SQL Agent's free-form `why_chosen` tended to be LLM-narrative boilerplate even when the schema technically passed validation. Making the planner own `why_chosen` via a structured trace + code-rendered string means boilerplate is *structurally impossible* — the reviewer sees an auditable derivation (candidates considered, grain match per candidate, scope feasibility per predicate) rather than a confident sentence.

R8's plan revision on reinvestigation closes the reviewer-feedback loop: when the reviewer correctly identifies a metric / aggregator / source / shape error, the LLM proposes a revised plan and the deterministic validator gates it. Before R8, the reviewer note flowed into the SQL Agent's correction context but not into the planner — so the SQL Agent would generate a correct new answer that pre-flight then rejected for plan-vs-answer mismatch.

### Agent Separation

The SQL Agent and Quality Agent remain independently testable and do not share internal state. The bridge between them is typed data: `QuestionPlan`, `SQLAgentAnswer`, `QualityReport`, `ReviewDecision`, `AuditHandoff`. The Quality Agent receives the SQL Agent's structured answer and the validated plan; it does not call back into the SQL Agent.

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

**Alternatives considered.** I evaluated three strategies:

1. *Raw column names only, no descriptions.* Rejected — the LLM cannot distinguish canonical from rollup sources, and the Q1/Q4 Ops-vs-canonical disagreements would silently leak through.
2. *Full schema dump (WrenAI-style; chosen).* Works for ≤50 models. Every column carries its description and every table its `⚠️` warnings. The trace validator already cross-checks against full metadata so retrieval is a future prompt-optimisation, not a correctness change.
3. *Embedding-based retrieval over dbt YAML (Tier 2 scaling work).* Right answer beyond ~50 models. Deliberately out of scope for the prototype's 7 tables; the architecture is ready for it (`metadata.py` is the only file that needs to change).

### Metric Registry

`metrics.yml` carries metric semantics:

- canonical primary source
- alternative sources
- period bounds
- aggregation grain
- breakdown dimension
- cross-source threshold
- plausibility bounds (`expected_min` / `expected_max`)
- Layer B notes (per-source semantic gotchas)

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
- "Month-on-month trend" becomes `period_over_period` and must return `month`, `gtv_idr`, `mom_change_idr`, and `mom_change_pct`.
- "Ops dashboard by asset class compared to canonical" becomes `breakdown_comparison` and must exclude `asset_class = 'Total'`.

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

## The Five Business Questions

Concrete summary of how each question routes:

| # | Question | Chosen source | Ambiguity surfaced? | Layer B verdict | Layer C trust |
|---|---|---|---|---|---|
| 1 | Total GTV (IDR) by asset class in October 2025 | `fct_trading_daily` | no | DISAGREEMENT (+13.71% vs mart_ops; pending) | RED |
| 2 | Total GTV (USD) in October 2025 | `fct_trading_daily` | no | DISAGREEMENT (+21.71% vs mart_ops; pending + fixed FX) | RED |
| 3 | Monthly Transacting Users (MTU) in October 2025 | three definitions, side-by-side | **yes — 3 definitions** | NOT_APPLICABLE (definitional, not reconciliation) | YELLOW |
| 4 | Transaction count by asset class in October 2025 | `fct_trading_daily` | no | DISAGREEMENT (+12.76% vs mart_ops; status filter) | RED |
| 5 | GTV MoM trend, October → December 2025 | `fct_trading_daily` | no | DISAGREEMENT (Dec stale Total ~3.46%) | RED |

**Q3 returns all three MTU definitions** (the case study's ambiguity-handling example):

- `aum_defined_mtu = 12,453`
- `raw_completed_unique_traders = 9,239`
- `mixpanel_mtu = 8,882`

**Q5 returns MoM trend fields**:

- October — `gtv_idr = 38,139,778,962`, `mom_change_pct = null` (no prior month)
- November — `gtv_idr = 38,704,082,670`, `mom_change_idr = +564,303,708`, `mom_change_pct = +1.48%`
- December — `gtv_idr = 38,604,717,992`, `mom_change_idr = -99,364,678`, `mom_change_pct = -0.26%`

Trust is RED on most questions because Layer B legitimately *disagrees* across sources. "RED with a HIGH-confidence grounded hypothesis explaining the disagreement" is the right answer — it means the reviewer sees both numbers and knows which is the canonical one. Silent green would have hidden the Ops dashboard divergence and the stale December Total.

---

## Quality Agent — Flag vs Hypothesis

The case study draws a sharp line: *flag first; hypothesise where you can; null is a valid output for the hypothesis*. The Quality Agent is structured to make that line visible end-to-end.

| Layer | Kind | Job | LLM? |
|---|---|---|---|
| **A** | FLAGS | Deterministic checks: non-empty, no required nulls, no zeros where must-be-positive, no negatives for always-positive metrics, plausibility range, required columns present, no aggregate members in breakdown, metric_value matches result rows, source matches plan. | no |
| **B** | FLAGS + HYPOTHESIS | Rules execute the same metric across the registry's alternative sources, compute deltas, decide AGREE / DISAGREEMENT / NOT_APPLICABLE (the **flags**). On disagreement, the LLM proposes a *grounded* hypothesis (the **suspicion**): must cite evidence from data + per-source notes, must declare confidence, must declare what the hypothesis does NOT explain. **May be null** when no grounded basis exists (e.g. Q3's MTU is definitional, not a data-quality disagreement). | yes (hypothesis only) |
| **C** | trust profile | Composes a reviewer-facing summary: dimensions (correctness / source_reliability / ambiguity) + overall RED/YELLOW/GREEN + reviewer_summary + unresolved_questions. LLM-driven with deterministic fallback when the LLM output is unparseable. | yes (with fallback) |

The reviewer panel surfaces this split explicitly. The "QA Summary" block at the top of the QA section reads:

```text
QA Summary — flag first, hypothesise where grounded
  FLAGS (what we know)          Layer A 9/9 pass  |  Layer B DISAGREEMENT — 3 findings (max |Δ| 13.71%)
  HYPOTHESIS (what we suspect)  HIGH — mart_ops_dashboard's higher GTV reflects pending transactions
                                being included via status != 'failed'.
```

When the hypothesis would be a guess, the system records *null with an absence note* rather than filling the field. See `outputs/sample/quality_report.json` for Q3, where `verdict=NOT_APPLICABLE` and the absence note explains that MTU has three definitions, not a single-source data-quality issue.

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
| `openai/gpt-4.1-mini` | 29 | 75,568 | `$0.0276` | Passed Q1–Q5 approve; demo-reject routed bad Q5 reinvestigation to audit. |
| `openai/gpt-4o-mini` | 59 | 161,596 | `$0.0233` | Passed Q1–Q5 approve; demo-reject was safely handled by reinvestigation/audit gates; extra Ops probe passed. |

I chose `openai/gpt-4o-mini` because it passed the hard gates, was cheaper in observed OpenRouter usage, and handled rejection paths without silently accepting bad source changes. The architecture does not depend on this model: the model is configurable through `OPENROUTER_MODEL`.

---

## How to Run

### Evaluator quickstart

```bash
git clone <repo> && cd pluang
bash setup.sh
# Edit .env: set OPENROUTER_API_KEY and PLUANG_DATA_DIR (path to provided CSVs).
pluang-agent setup
pluang-agent run --review-mode demo-approve
pluang-agent run --review-mode demo-reject
```

Both demo runs write evidence to `outputs/sample/` (overwriting existing committed samples). To validate without an API key, prefix any command with `PLUANG_LLM_MOCK=1`.

### Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | yes | — | OpenRouter auth. Never commit. |
| `OPENROUTER_MODEL` | no | `openai/gpt-4o-mini` | Model id; any OpenRouter model. |
| `OPENROUTER_BASE_URL` | no | `https://openrouter.ai/api/v1` | API endpoint. |
| `PLUANG_DATA_DIR` | yes | — | Path to the provided CSV directory. |
| `PLUANG_DB_PATH` | no | `var/pluang.sqlite` | Where the loader writes the SQLite DB. |
| `PLUANG_LLM_MOCK` | no | (unset) | `=1` → mock LLM client; tests + dev runs without an API key. |

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

### Load data

```bash
pluang-agent setup
```

The loader is idempotent: it drops/recreates the target SQLite tables, so rerunning setup does not duplicate rows.

### Run end-to-end

```bash
# Interactive human review
pluang-agent run

# Deterministic review demos for evidence generation
pluang-agent run --review-mode demo-approve
pluang-agent run --review-mode demo-reject
```

### Ask your own question

```bash
# Free-form question; metric + period inferred from the text
pluang-agent ask "What was total GTV (USD) in October 2025?"

# Skip interactive review (CI / scripted use)
pluang-agent ask "How many crypto transactions in November 2025?" --no-review

# Override the inferred metric or period
pluang-agent ask "Year-end snapshot" --metric gtv_idr --period "December 2025"
```

Ad-hoc outputs land in `outputs/ask/<synthesised_id>/` (gitignored — the five-question samples stay clean). When the planner can't construct a defensible derivation trace, the question routes to `audit_required` with a structured `AuditHandoff`. The CLI exits non-zero; *no question crashes the pipeline*, but not every question gets answered.

When the reviewer rejects an ad-hoc answer with `answer_wrong` or `source_wrong`, the planner LLM-revises the plan with the reviewer note (R8) before the SQL Agent retries.

### Check OpenRouter credit

```bash
pluang-agent cost
```

### Mock mode (no API key)

```bash
PLUANG_LLM_MOCK=1 pluang-agent run --review-mode demo-approve
```

Unit tests do not require an API key.

---

## Sample Outputs

Committed under `outputs/sample/`:

| File | Purpose |
|---|---|
| `question_plans.json` | Validated Planner output for all five required questions. |
| `sql_agent_answers.json` | SQL Agent answers from the final demo-approve run: source provenance, derivation trace, SQL, result rows, interpretation choices, usage. |
| `quality_report.json` | Quality Agent reports for all five answers: Layer A FLAGS, Layer B FLAGS + HYPOTHESIS, Layer C trust profile. |
| `review_approval.log` | Demo-approve review log (line summary). |
| `review_rejection_reinvestigation.log` | Demo-reject review log: Q5 rejected → R8 plan-revision attempted → pre-flight blocked the stale-source switch → `audit_required`. |
| **`review_reinvestigation_success.log`** | R8 happy path captured separately: rejection note "row count, not sum" → planner LLM-revises `SUM → COUNT_DISTINCT` → pre-flight passes → terminal = `reinvestigated`. Generated by `scripts/capture_reinvestigation.py`. |
| **`review_panel_rendered.txt`** | The interactive Rich review panel for Q1, rendered to text. This is what the human reviewer actually sees during the pause step (Key Facts + Derivation candidates + structured why_chosen + SQL + QA Summary FLAGS/HYPOTHESIS + Layer A/B/Hypothesis). Generated by `scripts/render_review_panel.py`. |
| `final_run_terminal.log` | Terminal transcript for the final ruff + pytest + demo-reject + demo-approve runs. |

### Inline preview — Q1 derivation trace (excerpt)

```json
{
  "required_grain": {"dimensions": ["transaction_date", "asset_class"]},
  "scope_predicates": ["October 2025"],
  "candidate_sources": [
    {"table": "fct_trading_daily", "grain_match": "exact", "selected": true},
    {"table": "agg_monthly_biz_summary", "grain_match": "rollup_needed", "selected": false,
     "rejection_reason": "Grain rollup needed; requires aggregation by transaction_date."},
    {"table": "mart_ops_dashboard", "grain_match": "rollup_needed", "selected": false,
     "rejection_reason": "Grain rollup needed; requires aggregation by transaction_date."}
  ],
  "chosen_source": "fct_trading_daily",
  "chosen_aggregator": "SUM",
  "aggregator_rationale": "SUM because gtv_idr is per-(transaction_date, asset_class) row, summable across days.",
  "chosen_filters": ["transaction_date >= '2025-10-01'", "transaction_date < '2025-11-01'"]
}
```

### Inline preview — Q1 Layer A + Layer B + Hypothesis (excerpt)

```text
Layer A FLAGS:  9/9 pass (non_empty, no_required_nulls, no_negative, no_zero, ...)
Layer B FLAGS:  DISAGREEMENT
  fct_trading_daily.gtv_idr (canonical)        Δ  0%      Reference.
  agg_monthly_biz_summary.gtv_idr               Δ  0%      Matches fct (sanity-checks the canonical).
  mart_ops_dashboard.gtv_idr                    Δ +13.71%  Ops mart filters status != 'failed' (includes pending).
HYPOTHESIS (HIGH): mart_ops_dashboard's higher GTV reflects pending transactions being included
                   via status != 'failed'.
  Evidence: Ops note "filters status != failed (includes pending). Expected to be HIGHER by ~10-15%";
            observed +13.71% within expected band; agg_monthly_biz_summary delta 0% confirms canonical.
  What this does NOT explain: None — the per-source notes fully explain the observed pattern.
```

### Inline preview — Rejection → reinvestigation transition (Q5 demo-reject)

```text
Question: q5_gtv_mom_trend_oct_dec_2025
Decision: reject
Category: source_wrong
Note: Trend answer should be reinvestigated because the December business summary Total row
      disagrees with fct_trading_daily.
Retry budget: used 2/2 (remaining 0)
Attempts: ['pre_flight_failure', 'pre_flight_failure', 'pre_flight_failure']
Terminal state: audit_required
Audit reason: auto_retry_exhausted
```

The system did exactly what it was designed to do: the reviewer asked for a source change, R8's revised plan tried the suggested source, pre-flight detected the empty-result symptom of the stale Total row, retries exhausted, terminal = `audit_required` with the full attempt history packaged for a human. *This is the value of refusing to silently fall back.*

The companion `review_reinvestigation_success.log` shows the other side of R8 — when the reviewer note unambiguously implies a plan-level change (`SUM → COUNT_DISTINCT`) and the revised plan validates, terminal = `reinvestigated`.

Historical pre-refactor outputs remain under `outputs/legacy_deterministic/` for development comparison only.

---

## Testing

```bash
pytest
ruff check .
```

Current status:

```text
160 passed, 1 warning
ruff clean
```

**Bi-directional test discipline** (hard rule from R6): every plan-driven check ships with a triggering test that fails AND an inverse test that passes the opposite scenario. Example: `aggregate_member_in_breakdown` has a triggering test (plan excludes `Total`, answer includes a `Total` row → fail) AND an inverse test (plan does NOT exclude `Total`, answer includes a `Total` row → pass). This prevents the "coded to Q1–Q5" failure mode — every mechanism must be plan-driven or data-driven, never question-id-driven.

Coverage highlights:

- `test_sql_runner.py` — read-only SQL guard rejects mutation, PRAGMA, and multi-statement SQL.
- `test_data_loader.py` — CSV loading is idempotent.
- `test_contracts.py` — Pydantic contracts round-trip.
- `test_planner.py` — Planner QA enforces MTU multi-definition, MoM fields, Ops/canonical comparison, Total-row exclusion.
- `test_planner_trace.py` — Derivation trace validator rejects hallucinated grains, missing rejection reasons, dangling chosen-source references, infeasible scope predicates, hallucinated filter columns, aggregator rationale without grain mention.
- `test_pre_flight.py` — bi-directional coverage of every plan-driven and trace-driven shape check (32 tests).
- `test_sql_attempt.py` — success path, exec failure → schema-hint correction → success, pre-flight failure → retry → success, budget exhaustion → SystemError.
- `test_replan_question.py` (R8) — reviewer note motivates plan change → revision validates; SQL-only complaint → plan unchanged via `revision_note`; hallucinated revision → validator rejects.
- `test_workflow_reinvestigation.py` (R8) — end-to-end replay of "row count, not SUM" reviewer-correction case → terminal = `reinvestigated`, not `audit_required`.
- `test_cli_ask.py` + `test_synthesize_question.py` — `pluang-agent ask` happy path, overrides honoured, planner failure exits non-zero.
- `test_layer_b.py` — cross-source reconciliation catches Ops status-filter deltas, fixed-FX USD deltas, stale December Total row.
- `test_layer_c.py` — LLM Layer C parses valid JSON and falls back safely on invalid output.
- `test_system_error_escalation.py` — auth/quota/transient/output failures route to audit rather than deterministic fallback.
- `test_review.py` — HITL panel formatter (Key Facts, Derivation panel, structured why_chosen, FLAGS-vs-HYPOTHESIS summary, pretty SQL).
- `test_golden_sql.py` — fixture-only canonical SQL verifies numeric truth on the real CSVs.

### Something that didn't work as expected

Three concrete failure modes I hit during development, and how the system was reshaped to prevent each from recurring:

**1. MTU multi-definition collapse.** Early prompt-only system returned a single number for MTU even though three valid definitions exist (AUM-derived, raw completed traders, Mixpanel). The LLM picked whichever was most prominent in the prompt that turn. Adding emphasis to the prompt made it less common but did not eliminate it. *Fix:* the planner contract now forces `answer_shape=multi_definition` for MTU, and pre-flight rejects any answer missing a required definition column. (`tests/test_planner.py::test_mtu_multi_definition_*`; `tests/test_pre_flight.py::test_missing_definition_column_fails`.) The output for Q3 is now structurally three columns, not one.

**2. The "reviewer-correction bounce-back" loop (motivated R8).** Before R8, a reviewer note like *"I want row count, not sum"* flowed only into the SQL Agent's correction context — the planner kept its old `aggregator=SUM`. The SQL Agent obediently wrote `COUNT_DISTINCT`, pre-flight detected `aggregator_mismatch` (plan says SUM, answer says COUNT), and the item bounced to `audit_required` *even though the reviewer was right*. The user-visible symptom: a correct reviewer note that the system could not act on. *Fix:* R8 added `planner.replan_question` — the planner LLM-revises the existing plan with the reviewer note as input, the deterministic validator gates the revision, and only then is the SQL Agent re-run. (`tests/test_workflow_reinvestigation.py`; `tests/test_replan_question.py`.) The captured artifact is `outputs/sample/review_reinvestigation_success.log`.

**3. LLM `why_chosen` narrative drift.** Early SQL Agent authored `source.why_chosen` as confident free-form prose ("I chose this table because it is the canonical source") with no structural defense — boilerplate even when the LLM didn't actually have good reason. *Fix:* R6's DerivationTrace. The planner owns `why_chosen` and code-renders it from a structurally validated trace (candidate_sources with `grain_match` + `rejection_reason` per candidate, `chosen_source`, `chosen_aggregator` with rationale, `chosen_filters`). The SQL Agent prompt explicitly instructs *not* to author `why_chosen`. Boilerplate is now structurally impossible. (`tests/test_planner_trace.py`.) In the reviewer panel, this surfaces as a structured key:value table instead of a wall of italic prose — see `outputs/sample/review_panel_rendered.txt`.

---

## Cost

Every live LLM call appends one JSON line to `logs/cost.jsonl`:

```json
{
  "ts": "2026-05-15T06:00:55Z",
  "stage_tag": "sql_agent:q5_gtv_mom_trend_oct_dec_2025",
  "model": "openai/gpt-4o-mini",
  "prompt_tokens": 4559,
  "completion_tokens": 450,
  "total_tokens": 5009,
  "cost_usd": 0.00067545
}
```

### Cost-saving choices

- Planner QA + semantic pre-flight: deterministic, no LLM.
- Layer A: deterministic, no LLM.
- Layer B: LLM only on disagreement (hypothesis generation), not on every answer.
- Layer C: LLM with deterministic fallback when the output is unparseable.
- SQL Agent: single LLM call per attempt; retry policy lives in the state machine, not in the agent. No deterministic answer fallback — failed output routes to retry or audit.

### Observed cost

- **Final 2026-05-15 audit run** (full demo-approve + demo-reject, all 5 questions twice): **22 calls, 75,166 tokens, $0.0125** — 0.25% of the $5 OpenRouter key budget.
- **Cumulative across all development runs**: **295 calls, 803,104 tokens, $0.0635** — 1.27% of the $5 key.

This is the cost evidence committed in `logs/cost.jsonl` (allow-listed in `.gitignore`).

---

## Scaling

Scaling is a routing problem: the architecture that handles the prototype's seven tables does not need to be the same architecture that handles a thousand. Below is the threshold router we'd build, with concrete triggers, what breaks first, and the code changes that come online at each tier.

### Tier 1 — current prototype scale (≤ 50 dbt models, ≤ 100 hand-curated metrics, single tenant)

Architecture as shipped. Full schema dump into the prompt; hand-curated `metrics.yml`; planner = registry lookup + heuristic fallback + LLM trace proposal + deterministic validator (hybrid).

| Bottleneck | First failure mode | Code path |
|---|---|---|
| Schema context size | Prompt exceeds context window at ~50 tables (~30K tokens of dbt YAML) | `metadata.describe_schema_context` |
| Metric registry curation | 1–2h human effort per metric | `metrics.yml`, `instructions.yml` |
| Per-question cost | ~$0.005 with the trace LLM call | `logs/cost.jsonl` |

### Tier 2 — growing dbt project (50–500 models, 100–1000 metrics, still single tenant)

What breaks first: schema context, planner coverage on unregistered metrics. What we'd ship:

1. **Schema retrieval.** Embed dbt YAML descriptions (model + column + warning). Top-K relevant tables get injected into the prompt instead of all of them. The trace validator already cross-checks against full metadata, so retrieval is a prompt-construction optimisation; the gate is untouched. — Touches `metadata.py`, adds `tests/test_schema_retrieval.py`.
2. **LLM planner candidate generator at scale.** The trace LLM call already proposes a `DerivationTrace`; for unregistered metrics, the same call also drafts the skeleton `QuestionPlan`. Validator stays deterministic. — Touches `planner.py`.
3. **Generic Layer B fallback.** When a metric is not in `metrics.yml`, Layer B infers candidate alternatives from `describe_schema_context()` instead of falling back to `NOT_APPLICABLE`. Coverage stays correlated with schema, not registry. — Touches `layer_b.py`.

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

### Ambitious vision

The three Tier 2/3 improvements compose into one architectural trajectory:

- **The Planner stays the gatekeeper.** Today's hybrid (LLM proposes trace → deterministic validator → render) is the same shape we'd run at 10K models. Validators get more rules; the gate doesn't move.
- **The metric registry stops being hand-curated.** Tier 3's dbt Semantic Layer adapter feeds the planner from MDL. The five-question demo's `metrics.yml` was illustrative; the real source of truth is upstream.
- **Audit is a signal, not a bin.** Tier 3's audit-feedback loop turns rejections into registry-tightening suggestions. The prototype's `AuditHandoff.unresolved_questions` is one row of an eventual dataset that the planner is trained against.

The architectural decision that buys us this trajectory is that the **Planner QA Gate, SQL execution, Pre-flight, Quality Agent, and Human Review are independently testable, independently swappable layers**. Retrieval, semantic-layer backends, and audit miners can replace internals without removing or weakening validation gates.

---

## Limitations — and what I'd ship first with another week

Each item is framed as a deliberate trade-off + future-value step, in priority order (top is highest leverage):

**1. No scaled evaluation harness.** *Deliberately bounded* because the case study scopes 5 questions; even the `ask` command is single-shot. The 5 questions are ground-truthable (golden SQL exists), but the system's claims of "robust across question types" rest on anecdote, not measurement. *Future value step:* a question-corpus harness that runs hundreds of NL questions sampled by complexity (single-table → joined → period-over-period → ambiguous) and reports plan-validation pass rate, pre-flight pass rate, Layer B disagreement coverage, end-to-end approval rate per reject-category. Without this, "robust" is a hypothesis, not a result.

**2. Scaling story is engineered, not measured.** The §Scaling tier story is honest about trade-offs but the system was never run against a >50-model fixture. *Deliberately bounded* by the prototype's data (7 tables) — and operationally the most reasonable "scaling answer" today would be each consuming team running its own narrow registry ("divide and conquer"). That works, but it's a workaround for the embedding-retrieval architecture we'd actually want. *Future value step:* materialise the Tier 2 schema-retrieval prototype against a 200-model dbt fixture and measure trace-validator pass rate vs. the current full-dump baseline. Only with that measurement can we credibly claim the centralised single-agent architecture beats the operational decomposition.

**3. UX is designed for a data team audience; other audiences have placeholders, not products.** The Rich panel + log files target an analyst-reviewer who reads SQL and knows what `SUM(gtv_idr)` means (an explicit case-study assumption — see `review.py` docstring). The system has the *substrate* for other audiences — typed contracts, structured panels, log-based audit trails — but no audience-specific UX yet. RevOps probably wants a "trust traffic light + plain English" view; Ops wants a "approve all green" sweep; engineering wants raw diff output. *Future value step:* an audience-router that selects between presentation layers off the same `PipelineItem`, validated with one walkthrough per audience. Operationally, this turns "the agent is for the data team" from a permanent constraint into a configurable starting point.

**4. Reviewer decisions are log-only.** *Deliberately bounded* because the prototype already produces line-by-line decision logs that evaluators can read. *Future value step:* an SQLite `review_decisions(question_id, reviewer_id, category, note, decision_at, terminal_state)` table feeding a weekly "registry tightening" report — turns `audit_required` from a hand-off into a learning signal (the Tier 3 audit-feedback loop in §Scaling).

**5. Plausibility bounds are hand-curated.** `expected_min/max` is set per metric in `metrics.yml` and the demo has 5 questions, so any statistical model would over-fit. *Deliberately bounded* by N. *Future value step:* once >3 months of history exist, replace the hand bounds with rolling per-metric std-dev windows — that catches *regression* (this month is unusually low for this metric) rather than just *implausibility* (numbers physically out of range).

**6. No external dashboard verification.** Layer B reconciles only sources registered in `metrics.yml` or referenced in the plan; it does not call out to the actual RevOps/Ops dashboards the analyst would compare against. *Deliberately bounded* by what a prototype could implement against fixed CSVs. *Future value step:* dashboard adapter as a Layer B source — the most expensive analyst-trust failure mode is "the system says 18.3B, the dashboard says 18.6B, and the analyst can't tell why" — that's exactly the gap a dashboard adapter would close.

**7. Reviewer-note quality still matters.** Vague rejection notes produce vague revisions. The validator catches *technically* invalid revisions (hallucinated tables, missing aggregator rationale) but cannot recover semantic intent that the reviewer didn't express. *Future value step:* a structured rejection form alongside the free-form note (radio + bullet checklist for common rejection patterns) reduces note variability without sacrificing the option to write prose.

**8. Hypothesis evidence is bounded by registered context.** Layer B's hypothesis can only cite what's in `metrics.yml.notes_for_layer_b` + the data it has just queried. A hypothesis about a downstream issue (Airflow scheduler, dbt run lateness, an upstream pipeline bug) would require richer signals (`dbt source freshness`, run-history APIs). *Deliberately bounded* — and the system records "null hypothesis" honestly rather than guessing. *Future value step:* freshness + run-history as Layer B inputs.

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
  review.py               Rich review panel (Key Facts + Derivation + QA Summary FLAGS/HYPOTHESIS)
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

outputs/sample/           final submission evidence (five demo questions + R8 success + panel render)
outputs/ask/              gitignored — per-invocation ad-hoc question artifacts
logs/cost.jsonl           allow-listed; evaluator-visible cost evidence

scripts/
  capture_reinvestigation.py  → outputs/sample/review_reinvestigation_success.log
  render_review_panel.py      → outputs/sample/review_panel_rendered.txt

tests/                    160 unit + integration tests
```

---

## Submission Notes

- No API key is committed (`.env` gitignored; `.env.example` is template only).
- Provided CSV data is not committed (`data/` gitignored).
- SQLite databases and local runtime files are ignored (`var/`, `*.sqlite`).
- `logs/cost.jsonl` is committed as evaluator-visible cost evidence.
- Sample outputs under `outputs/sample/` are regenerated by `pluang-agent run --review-mode demo-approve` + `demo-reject`; the two helper scripts under `scripts/` regenerate the R8 success log and the rendered panel.
