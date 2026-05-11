# SQL Agent — User Prompt Template

Variables (replaced in Python before sending):
- {schema_context}     — WrenAI-style full schema description from describe_schema_context()
- {registry_entry}     — Metric registry entry (yaml-style block) for this question
- {question_plan}      — Validated QuestionPlan JSON (skeleton phase output)
- {derivation_trace}   — Validated DerivationTrace JSON (R6) — empty if no trace
- {question_text}      — Plain-English business question
- {question_id}        — Stable question identifier (e.g. q1_gtv_idr_by_asset_oct_2025)
- {question_metric}    — Concise metric name
- {question_period}    — Time period (e.g. "October 2025")
- {reviewer_note}      — Empty string, or a reviewer note for reinvestigation
- {correction_block}   — Empty on the first attempt; populated on retries (## Correction block)

---

## Schema context (tables, columns, descriptions, ⚠️ warnings)

{schema_context}

---

## Metric registry entry

{registry_entry}

---

## Validated question plan

{question_plan}

---

## Validated derivation trace

{derivation_trace}

---

## Business question

{question_text}

Question id: {question_id}
Metric: {question_metric}
Period: {question_period}
{reviewer_note}
{correction_block}

## Instructions

Follow the Process from the system prompt: identify the registry entry above, use its primary source by default, apply its extra_filters, comply with the ⚠️ warnings on your chosen table, and surface ambiguity via interpretation_choices when applicable. If a `## Correction` block is present, address its specific failure detail rather than re-emitting the previous SQL. Return ONLY the JSON object — no explanation before or after.
