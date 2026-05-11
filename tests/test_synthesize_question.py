"""Tests for `questions.synthesize_business_question` (R7).

Bi-directional: every inference path has a triggering example and an
inverse — text the heuristic does match vs text it does not. Overrides
take precedence in every case.
"""

from __future__ import annotations

import pytest

from pluang_agent.questions import (
    _slugify,
    _synthesise_id,
    synthesize_business_question,
)

# ---------------------------------------------------------------------------
# Metric inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # Triggers
        ("What was total GTV (USD) in October 2025?", "gtv_usd"),
        ("Total GTV in October 2025", "gtv_idr"),
        ("Transaction count by asset class for October", "transaction_count"),
        ("MTU for October", "monthly_transacting_users"),
        ("Monthly transacting users in November", "monthly_transacting_users"),
        # Inverse (no keyword): fallback
        ("Tell me about user engagement", "adhoc_metric"),
        ("Average ticket size for premium customers", "adhoc_metric"),
    ],
)
def test_metric_inference_paths(text: str, expected: str) -> None:
    q = synthesize_business_question(text, now=1700000000)
    assert q.metric == expected


def test_metric_override_takes_precedence() -> None:
    """Even when text contains 'gtv', an explicit override wins."""
    q = synthesize_business_question(
        "Total GTV in October 2025",
        metric_override="custom_metric_name",
        now=1700000000,
    )
    assert q.metric == "custom_metric_name"


# ---------------------------------------------------------------------------
# Period inference
# ---------------------------------------------------------------------------


def test_single_month_period() -> None:
    q = synthesize_business_question(
        "What was GTV in October 2025?", now=1700000000
    )
    assert q.period == "2025-10-01 to 2025-11-01"


def test_multi_month_range_period() -> None:
    q = synthesize_business_question(
        "MoM trend from October 2025 to December 2025?", now=1700000000
    )
    assert q.period == "2025-10-01 to 2026-01-01"


def test_no_month_period_falls_back_to_unspecified() -> None:
    """INVERSE: question has no month-year token → period 'unspecified'."""
    q = synthesize_business_question(
        "Show me the latest numbers", now=1700000000
    )
    assert q.period == "unspecified"


def test_period_override_takes_precedence() -> None:
    q = synthesize_business_question(
        "GTV last quarter",
        period_override="Q4 2025",
        now=1700000000,
    )
    assert q.period == "Q4 2025"


# ---------------------------------------------------------------------------
# ID synthesis
# ---------------------------------------------------------------------------


def test_id_determinism_at_minute_resolution() -> None:
    """Two calls at the same minute with the same text → same id."""
    t = 1700000000  # 2023-11-14T22:13Z
    a = synthesize_business_question("What was GTV?", now=t)
    b = synthesize_business_question("What was GTV?", now=t)
    assert a.id == b.id


def test_id_differs_when_text_differs() -> None:
    """Different texts at the same minute → different ids (slug differs)."""
    t = 1700000000
    a = synthesize_business_question("What was crypto GTV in October 2025?", now=t)
    b = synthesize_business_question("How many MTU last month?", now=t)
    assert a.id != b.id


def test_id_format() -> None:
    qid = _synthesise_id("Total GTV in October 2025", now=1700000000)
    assert qid.startswith("adhoc_2023-11-14T22-13_")
    # slug part — six tokens max, lowercased, hyphenated
    slug = qid.split("_", 2)[-1]
    assert all(ch.isalnum() or ch == "-" for ch in slug)


def test_id_override_takes_precedence() -> None:
    q = synthesize_business_question(
        "x", id_override="my_custom_id", now=1700000000
    )
    assert q.id == "my_custom_id"


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


def test_slugify_strips_punctuation() -> None:
    assert _slugify("What was GTV (USD) in October 2025?") == "what-was-gtv-usd-in-october"


def test_slugify_six_token_cap() -> None:
    slug = _slugify("one two three four five six seven eight nine ten")
    assert slug == "one-two-three-four-five-six"


def test_slugify_empty_input_returns_placeholder() -> None:
    assert _slugify("") == "question"
    assert _slugify("!!!") == "question"


def test_slugify_truncates_to_60_chars() -> None:
    long_text = "supercalifragilisticexpialidocious " * 3
    slug = _slugify(long_text)
    assert len(slug) <= 60


# ---------------------------------------------------------------------------
# Whole-question construction
# ---------------------------------------------------------------------------


def test_text_is_stripped_and_preserved() -> None:
    q = synthesize_business_question(
        "  Total GTV in October 2025?  \n", now=1700000000
    )
    assert q.text == "Total GTV in October 2025?"


def test_empty_text_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        synthesize_business_question("", now=1700000000)
    with pytest.raises(ValueError, match="non-empty"):
        synthesize_business_question("   ", now=1700000000)


def test_full_overrides_combine() -> None:
    q = synthesize_business_question(
        "Anything",
        metric_override="m",
        period_override="p",
        id_override="i",
        now=1700000000,
    )
    assert q.id == "i"
    assert q.metric == "m"
    assert q.period == "p"
    assert q.text == "Anything"
