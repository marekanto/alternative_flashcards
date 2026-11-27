import os

from deepseek_client import chat

QA_MAX_OUTPUT_TOKENS = max(1800, int(os.getenv("QA_MAX_OUTPUT_TOKENS", "2000")))

SYSTEM = """You are an independent, conservative reviewer of an AltCards code diff.
Return ONLY JSON: {\"verdict\":\"pass\"|\"reject\"|\"needs_human_review\",\"findings\":[\"short items\"]}.

Review the actual diff against the user's explicit task and the repository rules.
Do not invent extra product requirements, ask for confirmation of an explicit request, or reject based on hypothetical risks unsupported by the diff or supplied source.

Interpret explicit UI copy requests literally:
- If the user asks to remove/hide a piece of text from a specific screen, removing that screen's rendered element is in scope. Do not require relocating the text unless the user asked for relocation or the diff creates a concrete functional problem.
- If the visible element uses a translation key such as t('login.subtitle'), removing its rendered use is sufficient. It is acceptable for the corresponding localized values to remain unused in the TR dictionary; do not require deleting the dictionary entry or prove the phrase is unused everywhere unless the task explicitly asks for global removal.
- If the user asks to remove italics, removing font-style:italic or its equivalent is sufficient. Do not require unrelated typography changes.

Reject concrete issues only: unrelated changes, secrets, new dependencies/network calls, weakened accessibility, broken mobile layout supported by evidence in the diff, or changes outside requested scope.
Reject changes to SRS, XP, daily limits, persistence, Sync, Supabase, authentication, account deletion or shared-deck behavior unless the task explicitly requires them.
Do not claim static review proves runtime correctness. Review only the task, diff and supplied repository context. Keep findings concise. If there is no concrete issue, return verdict pass."""


def review(task, diff):
    prompt = (
        "TASK (the user's complete explicit request; max 4000 chars):\n" + task
        + "\n\nDIFF TO REVIEW:\n" + diff[:10000]
        + "\n\nReturn JSON only. A concern must cite a concrete contradiction or regression visible in the task, diff, or supplied context."
    )
    return chat(SYSTEM, prompt, max_tokens=QA_MAX_OUTPUT_TOKENS)
