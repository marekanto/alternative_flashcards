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
      system_prompt, user_prompt — treść wiadomości.
      max_tokens  — limit odpowiedzi (default: MAX_OUTPUT_TOKENS z settings).
      model       — override modelu (default: MODEL z settings).
      thinking    — "enabled" / "disabled" / None.
                    None → używa THINKING z settings.
                    "enabled" → dołącza {"thinking": {"type": "enabled"}}
                    "disabled" → NIE dołącza pola thinking
                    (DeepSeek API nie zna wartości "disabled" — pominięcie
                    pola jest równoważne wyłączeniu rozumowania).
      json_mode   — True (default, zgodne z code_agent/qa_agent): wysyła
                    response_format={"type":"json_object"} i zwraca dict.
                    False: zwraca surowy string (np. do własnego parsowania).

    Zwraca:
      json_mode=True  → dict (sparsowany JSON)
      json_mode=False → str (surowa treść odpowiedzi)
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY GitHub secret")

    use_thinking = THINKING if thinking is None else thinking

    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": int(max_tokens or MAX_OUTPUT_TOKENS),
        "stream": False,
    }

    # DeepSeek przyjmuje wyłącznie {"type": "enabled"}; brak pola = disabled.
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
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek returned empty content")

    if not json_mode:
        return content

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"DeepSeek returned invalid JSON: {content[:600]}"
        ) from exc
