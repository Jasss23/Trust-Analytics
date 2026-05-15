# Pluang Analytics Agent — Project Instructions

This repository is the take-home submission for Pluang's Multi-Agent Analytics Reporting case study.

The README describes the system; this file describes the engineering invariants it must keep.

---

## Non-Negotiables

- Never commit API keys, `.env`, `.venv/`, the provided CSV data, SQLite databases, local working directories (`var/`), or generated logs (with one exception: `logs/cost.jsonl` is allow-listed because it is evaluator-visible evidence).
- Keep OpenRouter credentials environment-only via `OPENROUTER_API_KEY`. Never add hardcoded fallbacks.
- Keep `data/` gitignored. The setup command must accept a local data directory supplied at runtime.
- SQL execution is read-only. The `sql_runner.execute_read_only` guard rejects DDL, DML, PRAGMA, and multi-statement SQL.
- Do not silently fall back from the LLM path to deterministic answers. Hard errors (auth/quota/transient) escalate via `SystemError` → `AUDIT_REQUIRED(audit_reason='system_error')`. Soft errors (bad output / unsafe SQL) get one repair attempt, then escalate.
- Both agents must remain independently testable and not share internal state. The Quality Agent only receives the SQL Agent's structured answer; it does not call back into the SQL Agent.

---

## Architecture (current state)

| Component | File | Role |
|---|---|---|
| SQL Agent | `agents/sql_agent.py` | LLM-driven; takes the registry entry + schema context; produces a `SQLAgentAnswer` with full source provenance. |
| Quality Agent (orchestrator) | `agents/quality_agent.py` | Three-layer A→B→C orchestrator. |
| Layer A (rules) | `quality_rules.py` | Null/zero/negative/empty/required-fields/plausibility checks. High precision over recall. |
| Layer B (reconciliation) | `layer_b.py` | Rules execute the same metric across `metrics.yml` sources, compute deltas, decide AGREE/DISAGREEMENT/NOT_APPLICABLE. LLM proposes a *grounded* hypothesis on disagreement (must cite evidence; MAY return null). |
| Layer C (composition) | `layer_c.py` | LLM composes the `TrustProfile` (dimensions + overall + reviewer_summary + unresolved_questions) from structured A+B output. Falls back to rule-based composer when LLM unavailable or output invalid. |
| Workflow | `workflow.py` | LangGraph: answer → review → reinvestigate. Per-category retry-once. ESCALATED is folded into AUDIT_REQUIRED with `audit_reason`. AUDIT_REQUIRED ships an `AuditHandoff` package. |
| Review | `review.py` | Rich-formatted interactive panel — Layer B findings table, full SQL with syntax highlighting, hypothesis panel, interpretation_choices, source provenance. Note coaching with category-specific defaults on rejection. Post-pipeline reinvestigation diff via `render_reinvestigation_diffs()`. |
| LLM client | `llm.py` | OpenAI-compatible (OpenRouter target; OpenAI native works for dev). Typed errors, mock-mode (`PLUANG_LLM_MOCK=1`), cost log to `logs/cost.jsonl`. |
| Metric registry | `metrics.yml` + `metrics.py` | Hand-curated entries with primary/alternatives/period/breakdown/aggregator/threshold/expected_min/max/notes_for_layer_b. WrenAI-MDL-inspired thin slice. |
| Per-table warnings | `instructions.yml` + `metadata.py:load_instructions()` | Hand-curated YAML carrying `⚠️` warnings rendered into the schema context (Total-row, date format, status filter, canonical source). |
| Schema context | `metadata.py:describe_schema_context()` | WrenAI-style `### Model: name — description` blocks with full dbt column descriptions inlined. |
| Prompts | `prompts/*.md` | Process-driven, zero domain rule statements in the system prompt. |

---

## Case Study Behavioral Requirements (kept honest)

- The SQL Agent always returns a structured answer with `source.primary_table`, `source.why_chosen`, `source.alternatives_available`, `filters`, `assumptions`, `logic`, `sql`, `interpretation_choices`, `dq_notes`, and `usage` (when LLM ran).
- The SQL Agent surfaces ambiguity via `interpretation_choices` when a metric has multiple valid definitions. MTU questions specifically surface all three (AUM-derived, raw completed traders, Mixpanel).
- The Quality Agent produces a `QualityReport` for every answer, even clean ones (Layer A always runs; Layer B always runs, may return AGREE; Layer C always composes).
- Layer A reliably flags nulls in required fields, suspicious zeros, negative values for always-positive metrics, empty results, and out-of-range values per `expected_min`/`expected_max`.
- Layer B's hypothesis distinguishes what is *known* (the structured findings) from what is *suspected* (the LLM's grounded explanation, with confidence + `what_this_does_not_explain`).
- The human review step is a real pause and decision point. The reviewer sees a structured panel; rejections require a category + free-form note; the category drives routing; the note carries semantic context for the next agent attempt but is never parsed for routing.
- Sample outputs in `outputs/sample/` cover both approval and rejection-with-reinvestigation flows.

---

## Reviewer Rejection Routing

Reviewer rejections must include one of four categories plus a free-form note. The category drives routing; the note travels into the agent's next attempt as context (never parsed for routing).

| Category | Action |
|---|---|
| `answer_wrong` | Re-run SQL Agent with the reviewer note in context. |
| `source_wrong` | Re-run SQL Agent with the reviewer note in context (typically: "use alternative source X"). |
| `qa_insufficient` | Re-run Quality Agent (B + C) without changing the SQL Agent answer. |
| `external_disagreement` | Do NOT retry. Route directly to `audit_required`. |

Per-category retry limit = 1 (tracked on `ReviewDecision.per_category_retry_counts`). After the limit, the item routes to `audit_required` and an `AuditHandoff` package is emitted with all attempted answers, all reject notes, and Layer C's `unresolved_questions`.

`audit_required` is a hand-off, not a failure. The package gives the human enough context to act outside the system.

---

## Development Defaults

- Python CLI package under `src/pluang_agent/`.
- Tests under `tests/` — `pytest` runs without any API key (LLM calls use stub clients).
- Local runtime files under `var/` (gitignored).
- Committed sample artifacts under `outputs/sample/`.
- Default model: `openai/gpt-4o-mini` on OpenRouter, overrideable via `OPENROUTER_MODEL`. See README §Model Choice for the evaluation that led to this default.
- Mock fixtures in `tests/_fixtures/mock_llm/` (used by `PLUANG_LLM_MOCK=1` for interactive dev runs without an API key).
- Cost log at `logs/cost.jsonl` (allow-listed in `.gitignore`).

---

## When Editing the System

- **Adding a new question:** add an entry to `metrics.yml` (with `primary`, `alternatives`, `period_start/end`, `disagreement_threshold_pct`, optional `expected_min/max`); add it to `questions.REQUIRED_QUESTIONS`. No agent code changes.
- **Onboarding a new dataset:** drop in dbt `_sources.yml` / `_models.yml`, write `metrics.yml`, write `instructions.yml`. No prompt or agent changes.
- **Changing prompts:** edit `prompts/*.md`. The SQL Agent system prompt is intentionally domain-agnostic; new domain rules go in `instructions.yml` or `metrics.yml.notes_for_layer_b`, not in the prompt.
- **Adding a Layer A check:** add a `_check_X(answer)` function in `quality_rules.py` and append to `run_layer_a()`. Keep checks high-precision — false positives at Layer A poison reviewer trust.
- **Tightening Layer B reconciliation:** adjust `disagreement_threshold_pct` per metric, or add structured fields to `MetricEntry` and consume them in `layer_b._execute_source()`.
