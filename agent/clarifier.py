"""
agent/clarifier.py — AltCards

Czyta Issue + wszystkie komentarze (bez komentarzy bota) i decyduje:
  - READY                → można zacząć patch
  - NEEDS_CLARIFICATION  → agent pisze pytania, czeka na odpowiedź użytkownika

Pytania dotyczą WYŁĄCZNIE intencji użytkownika (co ma być widoczne,
kiedy, co ważniejsze) — NIE szczegółów kodu (to robi code_agent).

Wynik zapisywany do `clarification.json` w CWD.
"""
import json
import os
import subprocess
import sys

try:
    from .deepseek_client import chat
except ImportError:
    from deepseek_client import chat


BOT_LOGIN = "github-actions[bot]"


CLARIFIER_SYSTEM = """You are a CLARIFIER for an automated coding agent.

The user submits improvement requests for AltCards, a flashcards web app.
The app is a single index.html (HTML+CSS+JS) + Supabase backend.
A separate code agent will attempt to implement the task once it is clear.

Your job: decide whether the task is clear enough to attempt, or whether
you need to ask the user 1-3 clarifying questions first.

CRITICAL RULES:
1. Ask ONLY about USER INTENT and EXPECTED BEHAVIOR. Never ask about
   code internals — the code agent discovers those itself.

   GOOD questions:
     - "Should the bonus XP be visible immediately, or only after reload?"
     - "If server save fails, should we show an error, retry silently,
        or fall back to local-only?"
     - "Which is more important: XP persistence, or immediate UI refresh?"

   BAD questions (NEVER ask these):
     - "Which function should I modify?"
     - "Does the code have a save() function?"
     - "What is the current value of MAX_TOKENS?"

2. If the task names a concrete symptom AND expected result
   (e.g. "bonus XP disappears after reload — it should persist"),
   return status=ready, even if details are sparse.

3. If the task is vague ("fix XP", "improve leaderboard", "make it nicer"),
   return status=needs_clarification with SPECIFIC questions.

4. If PREVIOUS CLARIFICATION Q&A is already in the comments and the user
   has answered, do NOT ask the same questions again. Either accept their
   answers (status=ready) or ask genuinely NEW follow-up questions
   (status=needs_clarification, different from earlier).

5. Max 3 questions. Each must be answerable in one sentence.

OUTPUT (strict JSON):
{
  "status": "ready" | "needs_clarification",
  "questions": ["<q1>", "<q2>", ...],
  "reasoning": "<1 sentence why>"
}
"""


def gh(*args: str) -> str:
    """Uruchamia gh CLI i zwraca stdout."""
    result = subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True, check=True
    )
    return result.stdout


def fetch_issue(issue_number: str) -> dict:
    """
    Pobiera issue + wszystkie komentarze (bez komentarzy bota).
    Zwraca: {'title': str, 'body': str, 'comments': [str, ...]}
    """
    raw = gh("issue", "view", issue_number, "--json",
             "title,body,comments,author")
    data = json.loads(raw)

    comments = []
    for c in data.get("comments") or []:
        author = ((c.get("author") or {}).get("login") or "").lower()
        if author == BOT_LOGIN.lower():
            continue
        body = (c.get("body") or "").strip()
        if body:
            comments.append(body)

    return {
        "title": data.get("title") or "",
        "body": data.get("body") or "",
        "comments": comments,
        "author": (data.get("author") or {}).get("login") or "unknown",
    }


def build_user_prompt(issue: dict) -> str:
    parts = [
        "ISSUE TITLE:",
        issue["title"],
        "",
        "ISSUE BODY:",
        issue["body"] or "(empty)",
    ]
    if issue["comments"]:
        parts.extend([
            "",
            "USER FOLLOW-UP COMMENTS (in order):",
        ])
        for i, c in enumerate(issue["comments"], 1):
            parts.append(f"--- comment {i} ---")
            parts.append(c)
    parts.extend([
        "",
        "Decide: is this task clear enough to attempt?",
        "Return strict JSON as instructed.",
    ])
    return "\n".join(parts)


def decide(issue: dict) -> dict:
    user = build_user_prompt(issue)
    try:
        raw = chat(
            CLARIFIER_SYSTEM,
            user,
            thinking="disabled",   # proste rozumowanie, nie marnujmy tokenów
            max_tokens=800,
            json_mode=True,
        )
    except Exception as e:
        return {
            "status": "error",
            "questions": [],
            "reasoning": f"clarifier call failed: {e}",
        }

    # chat() zwraca dict, ale obsłużmy też str
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "questions": [],
                "reasoning": "clarifier returned non-JSON",
            }

    if not isinstance(raw, dict):
        return {
            "status": "error",
            "questions": [],
            "reasoning": "clarifier returned unexpected type",
        }

    status = str(raw.get("status", "error")).lower()
    if status not in ("ready", "needs_clarification"):
        status = "error"

    questions = raw.get("questions") or []
    if not isinstance(questions, list):
        questions = [str(questions)]
    questions = [str(q).strip() for q in questions if str(q).strip()][:3]

    if status == "needs_clarification" and not questions:
        status = "error"   # nie ma pytań = nie ma sensu pytać

    return {
        "status": status,
        "questions": questions,
        "reasoning": str(raw.get("reasoning") or "").strip(),
    }


def main():
    issue_number = os.environ.get("ISSUE_NUMBER")
    if not issue_number:
        print("ERROR: ISSUE_NUMBER env var missing", file=sys.stderr)
        sys.exit(1)

    issue = fetch_issue(issue_number)
    result = decide(issue)

    print("CLARIFIER_RESULT: " + json.dumps(result, ensure_ascii=False))

    with open("clarification.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if result["status"] == "error":
        print(f"ERROR: {result['reasoning']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
