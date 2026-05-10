# SQL Agent — User Prompt Template

Variables (replaced in Python before sending):
- {schema_context}     — WrenAI-style full schema description from describe_schema_context()
- {question_text}      — Plain-English business question
- {question_id}        — Stable question identifier (e.g. q1_gtv_idr_by_asset_oct_2025)
- {question_metric}    — Concise metric name
- {question_period}    — Time period (e.g. "October 2025")
- {reviewer_note}      — Empty string, or a reviewer note for reinvestigation

---

## Available schema and business context

{schema_context}

---

## Business question

{question_text}

Question id: {question_id}
Metric: {question_metric}
Period: {question_period}
{reviewer_note}

## Instructions

Using the schema context above and the source selection / date filter rules from the system prompt, write the SQL and return the JSON answer now. Return ONLY the JSON object — no explanation before or after.
