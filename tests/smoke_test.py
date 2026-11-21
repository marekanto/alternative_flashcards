"""
tests/smoke_test.py — AltCards
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from agent.patch_validator import SensitiveChangeNotApproved, enforce_sensitive_guard  # noqa: E402
from agent.context_builder import build_context, MAX_CONTEXT_CHARS, TARGET_FILE  # noqa: E402

SOURCE = (ROOT / TARGET_FILE).read_text(encoding="utf-8")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_generic_xp_task_is_complete() -> None:
    task = "Sprawdź dlaczego XP nie zwiększa się i czy może być gubione"
    res = build_context(task, SOURCE)
    ctx = res["context"]

    _assert(res["chars"] <= MAX_CONTEXT_CHARS,
            f"kontekst przekracza limit: {res['chars']} > {MAX_CONTEXT_CHARS}")
    _assert(not res["missing"], f"brakują bloki: {res['missing']}")
    _assert(not res["over_limit"], f"pominięto: {res['over_limit']}")

    required = [
        "function getLevelInfo", "function getRank", "function render",
        "function vStudy", "function vStats",
        "function ans", "function undoAnswer", "function grade",
        "async startSession", "async download", "async logXp",
        "queueUpload", "async upload", "async checkBonuses",
        "const defaults", "const save", "try{S=JSON.parse",
        "lvl-display", "S.settings.xp", "get_leaderboard", "xp_logs",
    ]
    for token in required:
        _assert(token in ctx, f"brak w kontekście XP: {token!r}")

    _assert(
        "return { level: lvl, currentXp: tempXp, nextLevelXp: req };" in ctx,
        "getLevelInfo ucięty — brak `return`",
    )
    _assert(
        "Sync?.queueUpload" in ctx,
        "save() ucięty — brak queueUpload",
    )


def test_ranking_bonus_task() -> None:
    task = "Dlaczego bonus XP za top 3 w rankingu dziennym nie jest widoczny w levelu?"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("checkBonuses", "get_leaderboard", "logXp",
                  "S.settings.xp", "serverXp", "xp_logs"):
        _assert(token in ctx, f"brak w kontekście bonusu: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


def test_login_task() -> None:
    task = "Usuń podtytuł na ekranie logowania i zmień tagline"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("function showLogin", "changeLoginLang",
                  "login.tagline", "login.subtitle"):
        _assert(token in ctx, f"brak w kontekście logowania: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


def test_brace_matcher_handles_templates_and_nested() -> None:
    from agent.context_builder import _match_braces
    sample = 'function f(){ const x = `a${ {b:1}.b }c`; if(1){return 2} }'
    start = sample.index("{")
    end = _match_braces(sample, start)
    _assert(end is not None, "matcher zwrócił None")
    _assert(sample[start:end].endswith("}"), "matcher nie domknął")


def test_sensitive_guard_requires_explicit_approval() -> None:
    before = "const defaults={newLimit:50,xp:0};"
    after = "const defaults={newLimit:50,dailyGoal:20,xp:0};"
    try:
        enforce_sensitive_guard(before, after, False)
    except SensitiveChangeNotApproved:
        pass
    else:
        raise AssertionError("protected defaults change passed without approval")

    enforce_sensitive_guard(before, after, True)


def test_validation_failure_refines_next_instruction() -> None:
    import orchestrator
    from unittest.mock import patch

    expected = "Narrowed instruction with exact correction"
    with patch.object(
        orchestrator,
        "refine_task",
        return_value={"ok": True, "refined_task": expected, "reasoning": "added validation feedback"},
    ) as mocked:
        actual = orchestrator._refine_after_failure(
            "original task", "previous instruction", "FIND text occurs 2 times",
            "(Patch rejected before a diff was created.)", 1,
        )
    _assert(actual == expected, "retry helper did not use the refined instruction")
    _assert(mocked.call_args.args[1] == [
        "Patch attempt 1 failed validation: FIND text occurs 2 times"
    ], "retry helper did not pass the validation error to the refiner")



def test_card_management_context_has_full_dependencies() -> None:
    task = """Przenieś akcje zarządzania taliami z Talie do Ustawień.

Zmiany w kodzie:
- Przerób dlist na wspólny renderer listy.
- Wyświetl listę w vCards i vSettings.
- Zachowaj obsługę shareDeck, unshareDeck i deckMenu.
- Zaktualizuj tut.g.decks i tut.g.settings.

Kryteria: po akcji save(); render(); obie listy są aktualne.

Nie zmieniaj
- SRS, XP, dzienny limit, undo, Sync, save/load, auth.
"""
    res = build_context(task, SOURCE)
    ctx = res["context"]
    _assert(res["ok"], f"kontekst talii niekompletny: missing={res['missing']} over={res['over_limit']}")
    for token in (
        "function vCards", "function vSettings", "function dlist",
        "function shareDeck", "function unshareDeck", "function deckMenu",
        "Sync.shareDeck(deck)", "Sync.unshareDeck(deck.sharedId)",
        "document.body.appendChild(m);", "'tut.g.decks':", "'tut.g.settings':",
    ):
        _assert(token in ctx, f"brak pełnej zależności w kontekście talii: {token!r}")
    _assert(not any(name in res["included"] for name in ("vStudy", "vStats", "ans", "undoAnswer", "grade")),
            "wykluczony XP/Undo nie powinien powodować dołączania kontekstu XP")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit kontekstu talii")


def test_agent_prompts_receive_complete_task() -> None:
    from unittest.mock import patch
    import code_agent
    import qa_agent

    task = "A" * 3900
    with patch.object(code_agent, "chat", return_value={"status": "need_context", "summary": "test"}) as code_chat:
        code_agent.propose(task, "source context")
        _assert(task in code_chat.call_args.args[1], "Code Agent task został obcięty")
    with patch.object(qa_agent, "chat", return_value={"verdict": "pass", "findings": []}) as qa_chat:
        qa_agent.review(task, "diff")
        _assert(task in qa_chat.call_args.args[1], "QA task został obcięty")

def main() -> int:
    tests = [
        test_brace_matcher_handles_templates_and_nested,
        test_sensitive_guard_requires_explicit_approval,
        test_validation_failure_refines_next_instruction,
        test_generic_xp_task_is_complete,
        test_ranking_bonus_task,
        test_login_task,
        test_card_management_context_has_full_dependencies,
        test_agent_prompts_receive_complete_task,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
