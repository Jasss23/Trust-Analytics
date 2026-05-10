"""One-off debug script: dump raw LLM responses + validation errors per question.

Used during Step 0 / Step 2 to ground prompt-tightening decisions in real
model output. Not part of the shipped CLI.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from pluang_agent.agents.sql_agent import SYSTEM_PROMPT, SQLAgent, _loads_json_object
from pluang_agent.config import load_settings
from pluang_agent.llm import OpenRouterClient
from pluang_agent.metadata import case_root_from_data_dir, load_dbt_metadata
from pluang_agent.models import SQLAgentAnswer
from pluang_agent.questions import REQUIRED_QUESTIONS


def main() -> None:
    settings = load_settings()
    metadata = load_dbt_metadata(case_root_from_data_dir(settings.data_dir))
    client = OpenRouterClient(settings)
    if not client.available:
        raise SystemExit("OPENROUTER_API_KEY is not set in .env")

    agent = SQLAgent(
        db_path=settings.db_path,
        metadata=metadata,
        llm_client=client,
        prefer_llm=True,
    )

    for question in REQUIRED_QUESTIONS:
        prompt = agent._build_prompt(question, reviewer_note=None)
        print("=" * 100)
        print(f"QUESTION: {question.id}")
        print("=" * 100)
        try:
            response = client.chat_json(SYSTEM_PROMPT, prompt)
        except Exception as exc:
            print(f"LLM call failed: {type(exc).__name__}: {exc}")
            continue

        print("--- RAW CONTENT ---")
        print(response.content[:4000])
        print("--- USAGE ---")
        print(response.usage.model_dump())

        try:
            payload = _loads_json_object(response.content)
        except json.JSONDecodeError as exc:
            print(f"--- JSON decode error: {exc} ---")
            continue

        try:
            SQLAgentAnswer.model_validate(payload)
            print("--- pydantic VALIDATED OK ---")
        except ValidationError as exc:
            print("--- pydantic ERRORS ---")
            for err in exc.errors():
                print(
                    f"  loc={'.'.join(str(p) for p in err['loc'])} "
                    f"type={err['type']} msg={err['msg']} input_repr={err.get('input')!r}"
                )
        print()


if __name__ == "__main__":
    main()
