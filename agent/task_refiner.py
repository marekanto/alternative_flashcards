"""
agent/task_refiner.py — AltCards

Czyta feedback QA z odrzuconej próby i produkuje WĄSKIE, konkretne zadanie
dla Code Agenta. Dokładnie to, co robi człowiek: "QA mówi, że X nie zostało
zmienione → dopisz X do zadania; QA mówi, że Y jest out of scope → zabroń Y".

Zakłada że chat(json_mode=True) zwraca dict (zgodnie z agent/deepseek_client.py).
Jeśli dostanie string — parsuje przez _extract_json jako fallback.
"""
from __future__ import annotations

import json
import re

try:
    from .deepseek_client import chat
except ImportError:
    from deepseek_client import chat


REFINER_SYSTEM = """You are a TASK REFINER for a code-fix pipeline.

The code agent produced a patch that was REJECTED by an independent QA reviewer.
Your job: turn the QA feedback into a SPECIFIC, SURGICAL instruction that the
code agent can execute so that the next patch PASSES QA.

You receive:
  - ORIGINAL_TASK: what the user asked for (must not be ignored)
  - ATTEMPT: which retry this is (1, 2, 3...)
  - PREVIOUS_DIFF: what the code agent actually changed (and failed)
  - QA_FINDINGS: why QA rejected it

RULES (follow strictly):
1. Preserve the ORIGINAL user intent — the bug must still be fixed.
2. Extract from QA_FINDINGS exactly what's MISSING. If QA says
   "the diff does not change X", then X MUST be in your refined task.
3. If QA says "Y is out of scope", explicitly forbid Y.
4. Be CONCRETE. Name the function, variable, and the operation. Examples:
   BAD:  "fix the XP persistence"
   GOOD: "In Sync.checkBonuses, call `const ok = await this.logXp(bonus);`
          BEFORE `S.settings.xp = (S.settings.xp||0) + bonus;`.
          If `ok` is false, `return;` without setting localStorage[key]."
5. No refactors, no cosmetic tweaks, no unrelated changes.
6. If QA findings suggest the previous attempt attacked the SYMPTOM (UI
   refresh) instead of the CAUSE (data persistence, ordering, side effects),
   you MUST redirect to the cause.
7. Max 400 words. Output only valid JSON.

OUTPUT (strict JSON):
{
  "refined_task": "<surgical instruction for code agent>",
  "reasoning": "<1-3 sentences: what specifically you added/redirected>",
  "explicitly_forbidden": ["<change not to make>", ...]
}
"""


def _extract_json(text: str) -> dict | None:
    """
    Fallback: wyciąga pierwszy zbalansowany obiekt JSON z tekstu.
    Używane gdy chat() zwróci string zamiast dict.
    """
    if not isinstance(text, str) or not text:
        return None

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _coerce_to_dict(raw) -> dict | None:
    """Ujednolica wynik chat(): dict → jak jest, str → parsuj, inne → None."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return _extract_json(raw)
    return None


def refine_task(
    original_task: str,
    qa_findings: list[str],
    previous_diff: str,
    attempt: int,
) -> dict:
    """
    Zwraca dict:
      {
        'refined_task': str,           # instrukcja dla Code Agenta
        'reasoning': str,              # 1-3 zdania wyjaśnienia
        'explicitly_forbidden': [str], # czego nie ruszać
        'ok': bool,                    # False = refiner zawiódł
      }
    """
    if not qa_findings:
        return {
            "refined_task": original_task,
            "reasoning": "no QA findings; keeping original task",
            "explicitly_forbidden": [],
            "ok": False,
        }

    diff_snip = previous_diff[:4000]
    if len(previous_diff) > 4000:
        diff_snip += "\n... [diff truncated]"

    # UWAGA: nie używamy backticków wokół diffu, bo psują markdown
    # w logach GitHuba i mylą model. Używamy jawnych markerów.
    user = (
        "ORIGINAL_TASK:\n"
        f"{original_task}\n\n"
        f"ATTEMPT:\n{attempt}\n\n"
        "PREVIOUS_DIFF (rejected):\n"
        "===BEGIN PREVIOUS_DIFF===\n"
        f"{diff_snip}\n"
        "===END PREVIOUS_DIFF===\n\n"
        "QA_FINDINGS:\n"
        + "\n".join(f"- {f}" for f in qa_findings)
        + "\n\nProduce the refined task as JSON."
    )

    try:
        raw = chat(
            REFINER_SYSTEM,
            user,
            thinking="enabled",
            max_tokens=1500,
            json_mode=True,
        )
    except Exception as e:
        return {
            "refined_task": original_task,
            "reasoning": f"refiner call failed: {e}",
            "explicitly_forbidden": [],
            "ok": False,
        }

    data = _coerce_to_dict(raw)
    if not data or not data.get("refined_task"):
        return {
            "refined_task": original_task,
            "reasoning": (
                "refiner returned invalid/missing 'refined_task'; "
                "keeping original task. Raw preview: "
                + (str(raw)[:300] if raw is not None else "None")
            ),
            "explicitly_forbidden": [],
            "ok": False,
        }

    refined = str(data["refined_task"]).strip()
    if not refined:
        return {
            "refined_task": original_task,
            "reasoning": "refiner returned empty 'refined_task'; keeping original",
            "explicitly_forbidden": [],
            "ok": False,
        }

    forbidden = data.get("explicitly_forbidden") or []
    if not isinstance(forbidden, list):
        forbidden = [str(forbidden)]
    forbidden = [str(x).strip() for x in forbidden if str(x).strip()]

    if forbidden:
        refined += "\n\nMUST NOT change: " + "; ".join(forbidden)

    reasoning = str(data.get("reasoning") or "").strip()
    if not reasoning:
        reasoning = "refiner produced refined task (no reasoning provided)"

    return {
        "refined_task": refined,
        "reasoning": reasoning,
        "explicitly_forbidden": forbidden,
        "ok": True,
    }
