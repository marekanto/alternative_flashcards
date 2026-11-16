from deepseek_client import chat

SYSTEM = """You are an independent reviewer of an AltCards code diff.
Return ONLY JSON: {"verdict":"pass"|"reject"|"needs_human_review","findings":["short items"]}.
Reject unrelated changes, secrets, new dependencies/network calls, weakened accessibility, broken mobile layout, or changes outside the requested scope.
Reject changes to SRS, XP, daily limits, persistence, Sync, Supabase, authentication, account deletion or shared-deck behavior unless the task explicitly requires them.
Do not claim static review proves runtime correctness. Review only the task and diff. Keep findings concise."""


def review(task, diff):
    return chat(SYSTEM, "TASK:\n" + task[:1800] + "\n\nDIFF:\n" + diff[:10000] + "\n\nJSON only.", max_tokens=900)
