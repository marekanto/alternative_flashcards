"""Minimal stdlib DeepSeek Chat Completions client."""
import json
import os
import urllib.error
import urllib.request

from settings import API_BASE, MODEL, THINKING, MAX_OUTPUT_TOKENS


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
        raise RuntimeError("Missing DEEPSEEK_API_KEY GitHub secret")

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

    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek API connection error: {exc}") from exc

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
        raise RuntimeError(f"DeepSeek returned no choices. Raw: {str(result)[:600]}")

    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    content = (choice.get("message") or {}).get("content")

    # --- KLUCZOWE: wykryj ucięcie ---
    if finish_reason == "length":
        got = usage.get("completion_tokens", "?")
        raise RuntimeError(
            f"DeepSeek output truncated: hit max_tokens={effective_max} "
            f"(generated {got} tokens, finish_reason=length). "
            f"Raise MAX_OUTPUT_TOKENS or narrow the task. "
            f"Content preview: {str(content)[:400]}"
        )

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            f"DeepSeek returned empty content (finish_reason={finish_reason}). "
            f"Raw: {str(result)[:400]}"
        )

    if not json_mode:
        return content

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"DeepSeek returned invalid JSON (finish_reason={finish_reason}): "
            f"{content[:600]}"
        ) from exc
