"""Shared Gemini client helper with transient retry, model fallback, and schema validation."""

import asyncio
from collections.abc import Awaitable, Callable
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
    cached_t = int(getattr(meta, "cached_content_token_count", 0) or 0)
    total_t = int(getattr(meta, "total_token_count", 0) or (prompt_t + output_t + thoughts_t))
    return {
        "model": model_name,
        "prompt_tokens": prompt_t,
        "output_tokens": output_t,
        "thoughts_tokens": thoughts_t,
        "cached_tokens": cached_t,
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
            cached_tokens=int(usage.get("cached_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )
    )


def _model_candidates(settings: Settings) -> list[str]:
    candidates: list[str] = []
    for m in [settings.gemini_model, *settings.gemini_fallback_model_list]:
        if m and m not in candidates:
            candidates.append(m)
    return candidates


async def _call_with_model_fallback(
    settings: Settings,
    *,
    contents: Any,
    config_factory: Callable[[bool], types.GenerateContentConfig],
    use_tools_enabled: bool = False,
) -> tuple[Any, str]:
    """Execute a Gemini generate_content call with model fallback and transient retry."""
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=12000),
    )
    candidates = _model_candidates(settings)
    last_exc: Exception | None = None
    allow_tools = use_tools_enabled

    for model_name in candidates:
        for attempt in range(2):
            config = config_factory(allow_tools)
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                return resp, model_name
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_exc = exc
                break
            except APIError as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                if allow_tools and code in (400, 429):
                    allow_tools = False
                    continue
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


async def generate_structured_with_usage(
    settings: Settings,
    *,
    prompt: str,
    response_schema: type[T],
    temperature: float = 0.4,
    use_search_grounding: bool = False,
    media_parts: list[types.Part] | None = None,
    system_instruction: str | None = None,
) -> tuple[T, dict[str, Any]]:
    """Call Gemini with structured JSON output and return `(parsed_model, usage_metadata_dict)`."""
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add your Gemini API key in .env to enable AI analysis."
        )

    use_search = bool(use_search_grounding and settings.gemini_enable_search_grounding)
    contents: str | list[str | types.Part] = (
        [prompt, *media_parts] if media_parts else prompt
    )
    resp, model_name = await _call_with_model_fallback(
        settings,
        contents=contents,
        use_tools_enabled=use_search,
        config_factory=lambda with_search: types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
            tools=(
                [types.Tool(google_search=types.GoogleSearch())]
                if with_search
                else None
            ),
        ),
    )
    parsed = response_schema.model_validate_json(resp.text)
    return parsed, _extract_usage(resp, model_name)


async def generate_agent_turn_with_tools(
    settings: Settings,
    *,
    system_instruction: str,
    user_text: str,
    tools: list[types.Tool],
    tool_executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    finish_tool_name: str = "complete_response",
    temperature: float = 0.35,
    media_parts: list[types.Part] | None = None,
    max_rounds: int = 3,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Run an on-demand Gemini function-calling agent turn and aggregate real token usage."""
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add your Gemini API key in .env to enable AI analysis."
        )

    initial_parts: list[types.Part] = [types.Part(text=user_text)]
    if media_parts:
        initial_parts.extend(media_parts)
    conv: list[types.Content] = [types.Content(role="user", parts=initial_parts)]

    agg: dict[str, Any] = {
        "model": "",
        "prompt_tokens": 0,
        "output_tokens": 0,
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "tool_calls": [],
    }

    for _ in range(max_rounds + 1):
        resp, model_name = await _call_with_model_fallback(
            settings,
            contents=conv,
            config_factory=lambda _unused: types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                tools=tools,
            ),
        )
        turn_usage = _extract_usage(resp, model_name)
        agg["model"] = model_name
        agg["prompt_tokens"] += turn_usage["prompt_tokens"]
        agg["output_tokens"] += turn_usage["output_tokens"]
        agg["thoughts_tokens"] += turn_usage["thoughts_tokens"]
        agg["cached_tokens"] += turn_usage["cached_tokens"]
        agg["total_tokens"] += turn_usage["total_tokens"]

        fcs = getattr(resp, "function_calls", None) or []
        if not fcs:
            reply_txt = (getattr(resp, "text", None) or "").strip()
            return None, reply_txt, agg

        cands = getattr(resp, "candidates", None) or []
        if cands and getattr(cands[0], "content", None) is not None:
            conv.append(cands[0].content)

        finish_args: dict[str, Any] | None = None
        fn_responses: list[types.Part] = []
        for fc in fcs:
            fc_name = str(getattr(fc, "name", "") or "")
            fc_args = dict(getattr(fc, "args", None) or {})
            if fc_name == finish_tool_name:
                finish_args = fc_args
                continue
            agg["tool_calls"].append(fc_name)
            tool_out = await tool_executor(fc_name, fc_args)
            fn_responses.append(
                types.Part.from_function_response(name=fc_name, response=tool_out)
            )

        if finish_args is not None:
            return finish_args, "", agg

        if fn_responses:
            conv.append(types.Content(role="tool", parts=fn_responses))
        else:
            break

    return None, "", agg


async def generate_structured_content(
    settings: Settings,
    *,
    prompt: str,
    response_schema: type[T],
    temperature: float = 0.4,
    use_search_grounding: bool = False,
    media_parts: list[types.Part] | None = None,
    system_instruction: str | None = None,
) -> T:
    """Backward-compatible wrapper returning only the parsed structured Pydantic object."""
    parsed, _ = await generate_structured_with_usage(
        settings,
        prompt=prompt,
        response_schema=response_schema,
        temperature=temperature,
        use_search_grounding=use_search_grounding,
        media_parts=media_parts,
        system_instruction=system_instruction,
    )
    return parsed
