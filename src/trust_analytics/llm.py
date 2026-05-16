"""LLM client wrapper with typed errors, mock mode, and cost logging.

Per spec 3.5: a single wrapper handles mocking, retry, error normalization,
and per-call cost logging. All LLM calls in the system go through this.

Mock mode (TRUST_ANALYTICS_LLM_MOCK=1) loads canned responses from
tests/_fixtures/mock_llm/<stage_tag>.json so the pipeline can run end-to-end
without a real key during dev. For unit tests, prefer injecting StubLLMClient
directly — it's clearer and per-test scoped.

Usage log: every real call appends one JSON line to logs/usage.jsonl with
provider/model/tokens/cost when available.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import openai
from openai import OpenAI

from trust_analytics.config import Settings
from trust_analytics.models import UsageRecord


class LLMError(RuntimeError):
    error_class: str = "llm_error"
    suggested_action: str = "Investigate the LLM call before retrying."


class LLMAuthError(LLMError):
    error_class = "auth"
    suggested_action = (
        "Verify OPENAI_API_KEY is set and authorised for the configured OPENAI_MODEL."
    )


class LLMQuotaError(LLMError):
    error_class = "quota"
    suggested_action = (
        "Add credit / lift the quota on the configured OPENAI_API_KEY, "
        "or switch to a different OpenAI project key, then re-run."
    )


class LLMTransientError(LLMError):
    error_class = "transient"
    suggested_action = "Wait briefly and re-run; if the error persists, check upstream provider status."


class LLMOutputError(LLMError):
    error_class = "output"
    suggested_action = (
        "The model returned unusable content. Inspect the raw response; "
        "consider a stricter schema instruction or a more capable model."
    )


SOFT_ERROR_TYPES: tuple[type[LLMError], ...] = (LLMOutputError,)
HARD_ERROR_TYPES: tuple[type[LLMError], ...] = (
    LLMAuthError,
    LLMQuotaError,
    LLMTransientError,
)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: UsageRecord


class LLMClient(Protocol):
    """Anything the rest of the system needs from an LLM client.

    Real (OpenAIClient), mock (FixtureLLMClient), and stub (StubLLMClient
    in tests) all conform to this protocol so agents accept any of them.
    """

    @property
    def available(self) -> bool: ...

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse: ...


class OpenAIClient:
    """Real LLM client targeting native OpenAI chat completions."""

    def __init__(self, settings: Settings, cost_log_path: Path | None = None):
        self.settings = settings
        self.cost_log_path = cost_log_path or Path("logs/usage.jsonl")

    @property
    def available(self) -> bool:
        return bool(self.settings.openai_api_key)

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        if not self.settings.openai_api_key:
            raise LLMAuthError("OPENAI_API_KEY is not set.")

        client = OpenAI(
            api_key=self.settings.openai_api_key,
        )
        kwargs: dict[str, Any] = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }

        try:
            response = client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise LLMAuthError(str(exc)) from exc
        except openai.PermissionDeniedError as exc:
            raise LLMAuthError(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise LLMQuotaError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise LLMTransientError(str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise LLMTransientError(str(exc)) from exc
        except openai.InternalServerError as exc:
            raise LLMTransientError(str(exc)) from exc
        except openai.BadRequestError as exc:
            raise LLMOutputError(f"Bad request to model: {exc}") from exc
        except openai.OpenAIError as exc:
            raise LLMTransientError(f"Unexpected upstream error: {exc}") from exc

        message = response.choices[0].message.content
        if not message:
            raise LLMOutputError("Model returned an empty response.")

        usage_data: dict[str, Any] = {}
        if response.usage is not None:
            usage_data = response.usage.model_dump()
        usage = UsageRecord(
            prompt_tokens=usage_data.get("prompt_tokens"),
            completion_tokens=usage_data.get("completion_tokens"),
            total_tokens=usage_data.get("total_tokens"),
            cost=_extract_cost(usage_data),
            model=self.settings.openai_model,
        )
        append_cost_log(
            self.cost_log_path,
            stage_tag=stage_tag,
            model=self.settings.openai_model,
            usage=usage,
            provider="openai",
        )
        return LLMResponse(content=message, usage=usage)

    def key_credit(self) -> dict[str, Any]:
        raise LLMOutputError("Native OpenAI does not expose a key credit endpoint.")


# Backward-compatible alias for older tests/imports while the public surface
# has moved to native OpenAI.
OpenAIClient = OpenAIClient


class FixtureLLMClient:
    """Mock client for interactive dev runs (TRUST_ANALYTICS_LLM_MOCK=1).

    Loads canned responses from a fixtures directory keyed by stage_tag.
    Each fixture file is a JSON document with `content` (the model's reply
    text) and `usage` (UsageRecord-shaped dict). Missing fixtures raise
    LLMOutputError so the caller's escalation path runs uniformly.
    """

    def __init__(self, fixtures_dir: Path):
        self.fixtures_dir = fixtures_dir
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        # stage_tag is e.g. "sql_agent:q1_gtv_idr_by_asset_oct_2025"
        slug = stage_tag.replace(":", "__") or "default"
        path = self.fixtures_dir / f"{slug}.json"
        if not path.is_file():
            raise LLMOutputError(
                f"No mock fixture for stage_tag={stage_tag} at {path}. "
                f"Generate one or use TRUST_ANALYTICS_LLM_MOCK=0 with a real key."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        usage = UsageRecord.model_validate(data.get("usage") or {})
        return LLMResponse(content=data["content"], usage=usage)


def make_client(settings: Settings) -> LLMClient:
    """Default client factory. Honors mock mode for interactive dev runs."""
    if os.getenv("TRUST_ANALYTICS_LLM_MOCK") == "1":
        fixtures_dir = Path(
            os.getenv("TRUST_ANALYTICS_LLM_FIXTURES")
            or "tests/_fixtures/mock_llm"
        )
        return FixtureLLMClient(fixtures_dir)
    return OpenAIClient(settings)


def append_cost_log(
    log_path: Path,
    *,
    stage_tag: str,
    model: str,
    usage: UsageRecord,
    provider: str = "openai",
) -> None:
    """Append one JSONL record to the cost log. Idempotent on directory creation."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider,
        "stage_tag": stage_tag,
        "model": model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": usage.cost,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _extract_cost(usage_data: dict[str, Any]) -> float | None:
    for key in ("cost", "total_cost"):
        value = usage_data.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None
