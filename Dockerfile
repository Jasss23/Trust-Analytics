FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY prompts ./prompts
COPY metrics.yml instructions.yml ./
COPY outputs/sample ./outputs/sample
COPY demo_data ./demo_data
COPY web ./web

RUN pip install --no-cache-dir -e .

RUN trust-analytics setup --data-dir demo_data/fintech_analytics/data --db-path var/trust_analytics.sqlite

CMD exec uvicorn trust_analytics.api:app --host 0.0.0.0 --port ${PORT}
