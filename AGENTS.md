# Trust Analytics Portal — Project Instructions

This repository is a product demo for a trustworthy analytics workflow. The
core promise is simple: business users see decision-ready answers, analysts see
evidence, and unresolved source conflicts become audit handoffs instead of
silent guesses.

## Invariants

- Keep credentials environment-only via `OPENAI_API_KEY`.
- Never commit `.env`, `.venv/`, local SQLite databases, `var/`, or generated
  runtime logs.
- Use only the synthetic fintech dataset committed under `demo_data/`.
- SQL execution remains read-only and single-statement.
- Do not silently fall back from failed live LLM answers to deterministic
  answers inside the agent chain; public API fallback must be explicit and
  labeled as a verified cached result.
- Keep business-facing UX separate from analyst evidence. SQL, derivation
  traces, and QA layers belong behind the review route, not the first screen.

## Product Surface

- `/` Decision Hub
- `/analysis/:id` Business Decision View
- `/review/:id` Analyst Evidence View
- `/handoff/:id` Audit Handoff View

The main demo story is: a business development owner prepares a one-slide
leadership decision pack from a trusted asset-class GTV analysis.

## Development Defaults

- Python package remains under `src/trust_analytics/` for implementation
  continuity; public CLI/package name is `trust-analytics`.
- FastAPI app: `trust_analytics.api:app`.
- Static React frontend: `web/`.
- Synthetic data: `demo_data/fintech_analytics/data`.
- Verified cache: `outputs/sample/`.
- Deployment target: GCP Cloud Run.
