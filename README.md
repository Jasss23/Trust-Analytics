# Trust Analytics Portal

A UX-led analytics portal for business decision owners who need to turn a
trusted SQL answer into an executive-ready decision pack.

The product demonstrates a credible analytics chain:

```text
business question
  -> planner + source derivation
  -> SQL agent
  -> read-only SQL execution
  -> semantic pre-flight
  -> quality checks and source reconciliation
  -> business view / analyst evidence / audit handoff
  -> CSV, PPTX, and email-ready outputs
```

The demo is intentionally framed as a generic fintech workflow. A business
development owner asks which asset class should be prioritised, sees whether
the result is decision-ready, and can generate a one-slide leadership brief.

## What The Demo Shows

- **Decision Hub**: recent analyses and verified cache entry points.
- **Business Decision View**: plain-English conclusion, chart, recommended use,
  source caveat, and actions.
- **Analyst Evidence View**: SQL, source provenance, derivation trace, and
  quality layers.
- **Audit Handoff View**: controlled handoff when source disagreement blocks a
  decision-ready answer.
- **Real exports**: CSV, PPTX, copyable executive summary, and email draft.
- **Live + cached runtime**: attempts a live OpenAI-backed run, then falls back
  to a verified cached result when live execution is unavailable.

## Run Locally

```bash
bash setup.sh
# Edit .env and set OPENAI_API_KEY for live runs.

source .venv/bin/activate
trust-analytics setup
uvicorn trust_analytics.api:app --reload --port 8080
```

If you do not activate the virtualenv, use the explicit binaries instead:

```bash
.venv/bin/trust-analytics setup
.venv/bin/uvicorn trust_analytics.api:app --reload --port 8080
```

Open [http://localhost:8080](http://localhost:8080).

The app ships with synthetic fintech data under `demo_data/fintech_analytics/`
and verified cached outputs under `outputs/sample/`.

## CLI

```bash
source .venv/bin/activate
trust-analytics setup
trust-analytics run --review-mode demo-approve
trust-analytics ask "What was total GTV by asset class in October 2025?" --no-review
trust-analytics cost
```

## Environment

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Native OpenAI API key for live agent runs. |
| `OPENAI_MODEL` | Model used for planner, SQL, and QA composition. Defaults to `gpt-4.1-mini`. |
| `TRUST_ANALYTICS_DATA_DIR` | Synthetic CSV data directory. Defaults to `demo_data/fintech_analytics/data`. |
| `TRUST_ANALYTICS_DB_PATH` | SQLite path. Defaults to `var/trust_analytics.sqlite`. |
| `TRUST_ANALYTICS_LLM_MOCK` | Set to `1` to use fixture responses. |

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Deployment health check. |
| `GET /api/analyses` | Verified cached analyses for the hub. |
| `POST /api/analysis/run` | Run live analysis and fall back to cache on failure. |
| `GET /api/analysis/{id}` | Cached business-facing analysis projection. |
| `GET /api/analysis/{id}/cached` | Explicit cached result. |
| `GET /api/analysis/{id}/export.csv` | Displayed result rows as CSV. |
| `GET /api/analysis/{id}/deck.pptx` | Executive slide plus evidence appendix. |
| `POST /api/analysis/{id}/email-draft` | Professional email draft content. |
| `GET /api/admin/costs` | Admin telemetry summary for run latency, tokens, and estimated cost. |
| `GET /api/admin/costs/{run_id}` | Stage/event detail for one telemetry run. |

## Test

```bash
ruff check .
pytest
```

For browser smoke testing, start the API server first, then run:

```bash
.venv/bin/python scripts/browser_smoke.py --url http://127.0.0.1:8080/
```

The smoke opens headless Chrome, clicks through Ask, Library, Evidence, Pack,
and Admin Cost flows, and writes screenshots to
`/private/tmp/trust-analytics-smoke/`.

## Deploy

See [deploy/gcp-cloud-run.md](deploy/gcp-cloud-run.md) for Cloud Run steps.

The deployed demo is unauthenticated and uses synthetic data only.
