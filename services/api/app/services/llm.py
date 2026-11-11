"""Thin Claude client for structured JSON tasks (context analysis, captions, overlay text).

* Structured outputs (``output_config.format`` with a JSON schema) guarantee parseable JSON.
* ``stop_reason == "refusal"`` is checked before reading content.
* Server-side refusal fallbacks are enabled (``fallbacks: "default"``) so a declined request is re-run on
  Anthropic's recommended fallback model inside the same call.
Every caller has a deterministic fallback, so the platform keeps working without an API key.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger("clipx.llm")


class LLMUnavailable(Exception):
    pass


@lru_cache
def _client() -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, max_retries=3, timeout=120.0)


def complete_json(system: str, user: str, schema: dict[str, Any], *, max_tokens: int = 4000) -> dict[str, Any]:
    s = get_settings()
    if not s.llm_configured:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
    import anthropic

    try:
        response = _client().beta.messages.create(
            model=s.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": s.llm_effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable(f"rate limited: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable(f"connection error: {exc}") from exc
    if response.stop_reason == "refusal":
        raise LLMUnavailable("model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable("response truncated")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
    if text is None:
        raise LLMUnavailable("no text block in response")
    return dict(json.loads(text))
