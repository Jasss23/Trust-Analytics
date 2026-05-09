# Pluang Analytics Agent Project Instructions

This repository is a take-home prototype for Pluang's Multi-Agent Analytics Reporting System. Keep the project small, runnable, and easy to review.

## Non-Negotiables

- Never commit API keys, `.env`, `.venv/`, provided CSV data, SQLite databases, local logs, or generated caches.
- Keep OpenRouter credentials environment-only. Use `OPENROUTER_API_KEY`; do not add fallback hardcoded keys.
- Keep `data/` gitignored. The setup command should accept a local data directory supplied by the evaluator.
- Preserve committed sample outputs once generated unless intentionally regenerating them after a behavior change.
- Keep the CLI runnable throughout development. Prefer adding or updating tests before changing behavior.
- Keep SQL execution read-only once the query layer exists.
- Keep the README evaluator-facing. It may start as a scaffold, but before submission it must describe the final implemented system rather than only the plan.
- Update the README whenever implementation or testing changes a design decision.
- Include an honest "what did not work as expected and how it was handled" note before final submission.

## Planned Architecture

- SQL Agent: receives a business question, selects source context, generates SQL, executes against local SQLite, and records source/logic provenance.
- Quality Agent: checks SQL Agent answers for deterministic data quality issues, cross-source disagreement, and grounded error hypotheses.
- Human Review: pauses the pipeline, surfaces context-rich review items, and accepts approval or structured rejection.
- LangGraph State Machine: orchestrates SQL Agent, Quality Agent, Human Review, one-time reinvestigation, and terminal states.

## Case Study Behavioral Requirements

- The SQL Agent must always return a structured answer with metric value, period, source tables, filters, SQL, assumptions, and logic/provenance notes.
- The SQL Agent must surface ambiguity when a question can be answered through multiple valid metric definitions or sources.
- The Quality Agent must produce a quality report for every SQL Agent answer, even when no issues are found.
- The Quality Agent must reliably flag nulls, suspicious zeros, negative values for always-positive metrics, empty results, missing required fields, and implausible results when detectable.
- The Quality Agent must clearly separate known facts from suspected causes. Flags are known observations; hypotheses are only included when grounded in available data or source metadata.
- The SQL Agent and Quality Agent must remain independently testable and must not rely on shared hidden state.
- The human review step must be a real pause and decision point, not only a printout at the end of the run.
- Sample outputs committed to `outputs/sample/` must include SQL Agent answers, Quality Agent report, approval flow, and rejection/reinvestigation flow.
- Final README must cover architecture, context/prompt strategy, model choice, how to run, scaling, testing, cost, and limitations.

## Reviewer Rejection Routing

Reviewer rejections must include a category and free-form note:

- `answer_wrong`: retry SQL Agent and Quality Agent once.
- `source_wrong`: retry SQL Agent with source guidance and Quality Agent once.
- `qa_insufficient`: retry Quality Agent once.
- `external_disagreement`: do not retry; move to `audit_required`.

Each item has a retry limit of one. After that, route to `audit_required`.

## Development Defaults

- Python CLI package under `src/pluang_agent/`.
- Tests under `tests/`.
- Local runtime files under `var/`.
- Committed sample artifacts under `outputs/sample/`.
- Default model: `openai/gpt-4.1-mini`, overrideable with `OPENROUTER_MODEL`.
