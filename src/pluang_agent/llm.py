"""OpenRouter client wrapper."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from pluang_agent.config import Settings
from pluang_agent.models import UsageRecord


class LLMError(RuntimeError):
    """Raised when the model call or model output fails."""


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: UsageRecord


class OpenRouterClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    def chat_json(self, system: str, user: str) -> LLMResponse:
        if not self.settings.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set.")

        client = OpenAI(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
        )
        response = client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            extra_body={"usage": {"include": True}},
        )
        message = response.choices[0].message.content
        if not message:
            raise LLMError("Model returned an empty response.")

        usage_data: dict[str, Any] = {}
        if response.usage is not None:
            usage_data = response.usage.model_dump()
        usage = UsageRecord(
            prompt_tokens=usage_data.get("prompt_tokens"),
            completion_tokens=usage_data.get("completion_tokens"),
            total_tokens=usage_data.get("total_tokens"),
            cost=_extract_cost(usage_data),
            model=self.settings.openrouter_model,
        )
        return LLMResponse(content=message, usage=usage)

    def key_credit(self) -> dict[str, Any]:
        if not self.settings.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set.")
        request = urllib.request.Request(
            f"{self.settings.openrouter_base_url.rstrip('/')}/key",
            headers={"Authorization": f"Bearer {self.settings.openrouter_api_key}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def _extract_cost(usage_data: dict[str, Any]) -> float | None:
    for key in ("cost", "total_cost"):
        value = usage_data.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None
