"""Business questions: the case-brief demo five PLUS ad-hoc question synthesis.

R7 added `synthesize_business_question()` so the `trust-analytics ask` CLI can
take free-form natural-language input. Inference reuses the planner's public
heuristic helpers (`planner.infer_metric_intent`, `planner.infer_period_from_text`).
"""

from __future__ import annotations

import re
import time

from trust_analytics.models import BusinessQuestion

REQUIRED_QUESTIONS = [
    BusinessQuestion(
        id="q1_gtv_idr_by_asset_oct_2025",
        text="What was total GTV (IDR) by asset class in October 2025?",
        metric="gtv_idr_by_asset_class",
        period="October 2025",
    ),
    BusinessQuestion(
        id="q2_gtv_usd_oct_2025",
        text="What was total GTV (USD) in October 2025?",
        metric="total_gtv_usd",
        period="October 2025",
    ),
    BusinessQuestion(
        id="q3_mtu_oct_2025",
        text="How many Monthly Transacting Users (MTU) were there in October 2025?",
        metric="monthly_transacting_users",
        period="October 2025",
    ),
    BusinessQuestion(
        id="q4_transaction_count_by_asset_oct_2025",
        text="How did transaction count compare across asset classes in October 2025?",
        metric="transaction_count_by_asset_class",
        period="October 2025",
    ),
    BusinessQuestion(
        id="q5_gtv_mom_trend_oct_dec_2025",
        text="What was the month-on-month GTV trend from October to December 2025?",
        metric="gtv_idr_month_on_month_trend",
        period="October 2025 to December 2025",
    ),
]


def get_question(question_id: str) -> BusinessQuestion:
    for question in REQUIRED_QUESTIONS:
        if question.id == question_id:
            return question
    raise KeyError(f"Unknown question id: {question_id}")


# ---------------------------------------------------------------------------
# R7: ad-hoc question synthesis
# ---------------------------------------------------------------------------


def synthesize_business_question(
    text: str,
    *,
    metric_override: str | None = None,
    period_override: str | None = None,
    id_override: str | None = None,
    now: float | None = None,
) -> BusinessQuestion:
    """Build a `BusinessQuestion` from free-form text.

    Used by the `trust-analytics ask` CLI surface. When metric or period are
    not supplied, infers them from the text via the planner's public
    heuristic helpers. Generates a timestamped slug id so concurrent
    ad-hoc invocations don't clash and so the `outputs/ask/<id>/` directory
    is human-recognisable.

    `now` is injectable for deterministic tests; defaults to current UTC.
    """
    # Local import to avoid circular dependency at module load (planner
    # imports from models, which models doesn't reference back to).
    from trust_analytics.planner import infer_metric_intent, infer_period_from_text

    if not text or not text.strip():
        raise ValueError("Question text must be non-empty.")

    metric = metric_override or infer_metric_intent(text)
    if period_override:
        period = period_override
    else:
        parsed = infer_period_from_text(text)
        period = (
            f"{parsed.start} to {parsed.end}" if parsed else "unspecified"
        )
    qid = id_override or _synthesise_id(text, now=now)
    return BusinessQuestion(id=qid, text=text.strip(), metric=metric, period=period)


def _synthesise_id(text: str, *, now: float | None = None) -> str:
    """`adhoc_YYYY-MM-DDTHH-MM_<slug>` — minute-granularity timestamp + a
    six-token lowercase slug of the question text.

    Minute granularity is enough for two reasons: (1) concurrent ad-hoc
    invocations within the same minute are vanishingly rare for a CLI, and
    (2) the slug already carries enough text-derived entropy that two
    different questions in the same minute will get different ids.
    """
    timestamp = time.gmtime(now) if now is not None else time.gmtime()
    stamp = time.strftime("%Y-%m-%dT%H-%M", timestamp)
    slug = _slugify(text)
    return f"adhoc_{stamp}_{slug}"


def _slugify(text: str) -> str:
    """First six tokens, lowercase, non-alphanumeric → '-'. Trimmed to 60
    chars total to keep filenames sane."""
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9]+", lowered)[:6]
    slug = "-".join(tokens) or "question"
    return slug[:60]

