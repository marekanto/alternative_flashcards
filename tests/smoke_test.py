"""
tests/smoke_test.py — AltCards
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


def main() -> int:
    tests = [
        test_brace_matcher_handles_templates_and_nested,
        test_generic_xp_task_is_complete,
        test_ranking_bonus_task,
        test_login_task,
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
