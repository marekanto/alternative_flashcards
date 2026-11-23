"""Minimal stdlib DeepSeek Chat Completions client."""
import json
import os
import socket
import time
import urllib.error
import urllib.request

try:
    from .settings import API_BASE, MODEL, THINKING, MAX_OUTPUT_TOKENS
except ImportError:
    from settings import API_BASE, MODEL, THINKING, MAX_OUTPUT_TOKENS


RETRYABLE_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
MAINTENANCE_HTTP_CODES = {408, 425, 500, 502, 503, 504}
API_MAX_ATTEMPTS = int(os.getenv("DEEPSEEK_API_MAX_ATTEMPTS", "3"))


class DeepSeekInfrastructureError(RuntimeError):
    """A transient transport or upstream server failure that may merit repair."""


class DeepSeekConfigurationError(RuntimeError):
    """A credential or request configuration error; repository repair is not appropriate."""


class DeepSeekResponseError(RuntimeError):
    """The API responded, but its payload was unusable or incomplete."""


def chat(
    system_prompt: str,
    user_prompt: str,
    max_tokens=None,
    model=None,
    thinking=None,
    json_mode=True,
):
    """
    Wywołanie DeepSeek Chat Completions.

    Parametry:
      thinking  — "enabled" / "disabled" / None (None = THINKING z settings)
      json_mode — True: response_format=json_object, zwraca dict
                  False: zwraca string
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekConfigurationError("Missing DEEPSEEK_API_KEY GitHub secret")

    use_thinking = THINKING if thinking is None else thinking
    effective_max = int(max_tokens or MAX_OUTPUT_TOKENS)

    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": effective_max,
        "stream": False,
    }

    if use_thinking == "enabled":
        payload["thinking"] = {"type": "enabled"}

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        API_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    result = None
    max_attempts = max(1, API_MAX_ATTEMPTS)
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            message = f"DeepSeek API HTTP {exc.code}: {detail}"
            if exc.code not in RETRYABLE_HTTP_CODES:
                raise DeepSeekConfigurationError(message) from exc
            if attempt >= max_attempts:
                if exc.code in MAINTENANCE_HTTP_CODES:
                    raise DeepSeekInfrastructureError(message) from exc
                # HTTP 429 is retried, but rate limiting is not safely repairable
                # by changing repository code.
                raise DeepSeekConfigurationError(message) from exc
            print(f"{message}; retry {attempt}/{max_attempts - 1}")
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            message = f"DeepSeek API connection error: {exc}"
            if attempt >= max_attempts:
                raise DeepSeekInfrastructureError(message) from exc
            print(f"{message}; retry {attempt}/{max_attempts - 1}")
        # Backoff: 2s, 4s; capped to avoid long workflow stalls.
        time.sleep(min(2 ** attempt, 8))

    usage = result.get("usage", {})
    details = usage.get("prompt_tokens_details", {})
    print(
        "DEEPSEEK_USAGE "
        + json.dumps({
            "model": model or MODEL,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cache_hit_tokens": details.get("prompt_cache_hit_tokens"),
            "cache_miss_tokens": details.get("prompt_cache_miss_tokens"),
        }, ensure_ascii=False)
    )

    choices = result.get("choices") or []
    if not choices:
        raise DeepSeekResponseError(f"DeepSeek returned no choices. Raw: {str(result)[:600]}")

    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    content = (choice.get("message") or {}).get("content")

    # --- KLUCZOWE: wykryj ucięcie ---
    if finish_reason == "length":
        got = usage.get("completion_tokens", "?")
        raise DeepSeekResponseError(
            f"DeepSeek output truncated: hit max_tokens={effective_max} "
            f"(generated {got} tokens, finish_reason=length). "
            f"Raise MAX_OUTPUT_TOKENS or narrow the task. "
            f"Content preview: {str(content)[:400]}"
        )

    if not isinstance(content, str) or not content.strip():
        raise DeepSeekResponseError(
            f"DeepSeek returned empty content (finish_reason={finish_reason}). "
            f"Raw: {str(result)[:400]}"
        )

    if not json_mode:
        return content

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekResponseError(
            f"DeepSeek returned invalid JSON (finish_reason={finish_reason}): "
            f"{content[:600]}"
        ) from exc
