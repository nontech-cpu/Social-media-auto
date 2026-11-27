"""Shared Gemini client helper with transient retry, model fallback, and schema validation."""

import asyncio
from typing import Any, TypeVar

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import AIUsageLog

T = TypeVar("T", bound=BaseModel)


def _is_retryable_or_unavailable(exc: APIError) -> bool:
    code = getattr(exc, "code", None)
    msg = str(exc).lower()
    return code in (404, 429, 500, 503) or "unavailable" in msg or "high demand" in msg


def _extract_usage(resp: Any, model_name: str) -> dict[str, Any]:
    meta = getattr(resp, "usage_metadata", None)
    prompt_t = int(getattr(meta, "prompt_token_count", 0) or 0)
    output_t = int(getattr(meta, "candidates_token_count", 0) or 0)
    thoughts_t = int(getattr(meta, "thoughts_token_count", 0) or 0)
    total_t = int(getattr(meta, "total_token_count", 0) or (prompt_t + output_t + thoughts_t))
    return {
        "model": model_name,
        "prompt_tokens": prompt_t,
        "output_tokens": output_t,
        "thoughts_tokens": thoughts_t,
        "total_tokens": total_t,
    }


def record_ai_usage(
    db: AsyncSession,
    *,
    account_id: str | None,
    feature: str,
    usage: dict[str, Any],
) -> None:
    """Attach an AIUsageLog row to the current session so real Gemini token costs are tracked."""
    db.add(
        AIUsageLog(
            account_id=account_id,
            feature=feature,
            model=str(usage.get("model") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            thoughts_tokens=int(usage.get("thoughts_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )
    )


async def generate_structured_with_usage(
    settings: Settings,
    *,
    prompt: str,
    response_schema: type[T],
    temperature: float = 0.4,
    use_search_grounding: bool = False,
    media_parts: list[types.Part] | None = None,
) -> tuple[T, dict[str, Any]]:
    """Call Gemini with structured JSON output and return `(parsed_model, usage_metadata_dict)`."""
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add your Gemini API key in .env to enable AI analysis."
        )

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=12000),
    )
    candidates: list[str] = []
    for m in [settings.gemini_model, *settings.gemini_fallback_model_list]:
        if m and m not in candidates:
            candidates.append(m)

    tools = (
        [types.Tool(google_search=types.GoogleSearch())]
        if (use_search_grounding and settings.gemini_enable_search_grounding)
        else None
    )

    contents: str | list[str | types.Part] = (
        [prompt, *media_parts] if media_parts else prompt
    )
    last_exc: Exception | None = None
    for model_name in candidates:
        for attempt in range(2):
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=temperature,
                tools=tools,
            )
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                parsed = response_schema.model_validate_json(resp.text)
                return parsed, _extract_usage(resp, model_name)
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_exc = exc
                break  # immediately fall back to next model on timeout
            except APIError as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                # If search grounding is rejected (400) or rate-limited (429), retry cleanly without search tools
                if tools is not None and code in (400, 429):
                    tools = None
                    continue
                # Immediately try next fallback model on 503/404 instead of waiting on an overloaded model
                if code in (404, 503) or "unavailable" in str(exc).lower():
                    break
                if _is_retryable_or_unavailable(exc):
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    break
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No Gemini model candidates configured.")


async def generate_structured_content(
    settings: Settings,
    *,
    prompt: str,
    response_schema: type[T],
    temperature: float = 0.4,
    use_search_grounding: bool = False,
    media_parts: list[types.Part] | None = None,
) -> T:
    """Backward-compatible wrapper returning only the parsed structured Pydantic object."""
    parsed, _ = await generate_structured_with_usage(
        settings,
        prompt=prompt,
        response_schema=response_schema,
        temperature=temperature,
        use_search_grounding=use_search_grounding,
        media_parts=media_parts,
    )
    return parsed
