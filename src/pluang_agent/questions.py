"""Business questions from the case brief."""

from __future__ import annotations

from pluang_agent.models import BusinessQuestion

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

